#!/usr/bin/env ruby
# Normalize bottle data only. Never install or execute the supplied Mach-O.
require "json"
require "digest"
require "pathname"
require "find"
require "tempfile"

ROOT = File.expand_path("../..", __dir__)
ASAN_PATH = "llvm/23.1.4/lib/clang/23/lib/darwin/libclang_rt.asan_osx_dynamic.dylib"
ASAN_ID = "@rpath/libclang_rt.asan_osx_dynamic.dylib"
ASAN_SOURCE_SHA = "7c8af9f0737137dcb777c0d964753a1161f00a8314450dc4fc5eff6a1728af87"
ASAN_SOURCE_BYTES = 3966096
ASAN_RESULT_SHA = "8c8883d8ca701c5fb8001291a00e4e5b68976696db330fd5c3adacbfbc1e0ba4"
ASAN_RESULT_BYTES = 1115280
def sha(bytes) = Digest::SHA256.hexdigest(bytes)

def regular(path, limit)
  current = ROOT
  Pathname.new(path.delete_prefix("#{ROOT}/")).each_filename do |part|
    current = File.join(current, part)
    raise "symlink file component: #{current}" if File.lstat(current).symlink?
  end
  stat = File.lstat(path)
  raise "regular unlinked file required: #{path}" unless stat.file? && stat.nlink == 1
  raise "file size exceeds limit: #{path}" unless stat.size.between?(1, limit)
  [File.binread(path), stat]
end

def audit_library
  lock = JSON.parse(regular("#{ROOT}/plan/upstream.lock.json", 1 << 20).first)
  sources = lock.fetch("sources").select { |s| s["id"] == "ruby_macho" }
  raise "missing or duplicate ruby_macho source pin" unless sources.size == 1
  source = sources.first
  snapshot = { "destination" => "references/ruby-macho", "manifest" => "references/ruby-macho.manifest.json", "include" => ["lib", "LICENSE"] }
  raise "unsupported ruby_macho source pin" unless source["version"] == "7.0.0" && source["snapshot"] == snapshot && source["commit"].match?(/\A[0-9a-f]{40}\z/)
  manifest = JSON.parse(regular("#{ROOT}/#{snapshot['manifest']}", 1 << 20).first)
  expected_source = source.slice("id", "repository", "commit", "snapshot")
  raise "capture differs from source pin" unless manifest["schema_version"] == 1 && manifest["source"] == expected_source && manifest["destination"] == snapshot["destination"]
  entries = manifest.fetch("files")
  raise "invalid capture count" unless entries.size.between?(1, 256)
  expected = {}
  entries.each do |entry|
    path = entry.fetch("path")
    raise "invalid capture path" unless path.is_a?(String) && path.split("/").all? { |c| c.match?(/\A[A-Za-z0-9_.-]+\z/) && ![".", ".."].include?(c) } && !path.start_with?("/") && !expected.key?(path)
    raise "invalid capture entry" unless entry.keys.sort == %w[mode path sha256] && entry["mode"] == "100644" && entry["sha256"].match?(/\A[0-9a-f]{64}\z/)
    expected[path] = entry
  end
  base = "#{ROOT}/#{snapshot['destination']}"
  allowed_dirs = [base] + expected.keys.flat_map { |p| a = p.split("/"); (1...a.size).map { |n| "#{base}/#{a.first(n).join('/')}" } }
  actual = {}
  Find.find(base) do |path|
    stat = File.lstat(path)
    if stat.directory?
      raise "unexpected capture directory" unless allowed_dirs.include?(path)
      next
    end
    bytes, stat = regular(path, 2 << 20)
    rel = path.delete_prefix("#{base}/")
    actual[rel] = { "path" => rel, "mode" => format("100%03o", stat.mode & 0o7777), "sha256" => sha(bytes) }
  end
  raise "capture inventory mismatch" unless actual == expected
  source
end

# Preserve only a validated, narrow ad-hoc layout. Opaque allocated tail bytes
# belong to the authenticated input; this policy never synthesizes padding.
def preserved_adhoc(macho, bytes)
  raise "preserved ad-hoc layout requires a dylib" unless macho.dylib?
  commands = macho[:LC_CODE_SIGNATURE]
  raise "one terminal signature required" unless commands.size == 1
  command = commands.first
  offset, allocation = command.dataoff, command.datasize
  raise "invalid signature bounds" unless offset >= 32 + macho.sizeofcmds && offset % 16 == 0 &&
                                          allocation >= 36 && offset + allocation == bytes.bytesize
  linkedit = macho.segments.select { |segment| segment.segname == "__LINKEDIT" }
  raise "invalid signature segment layout" unless linkedit.one? && linkedit.first.fileoff <= offset &&
    linkedit.first.fileoff + linkedit.first.filesize == bytes.bytesize
  signature = bytes.byteslice(offset, allocation)
  magic, length, count = signature.unpack("N3")
  raise "unsupported ad-hoc SuperBlob" unless magic == 0xfade0cc0 && length.between?(36, allocation) && count == 3
  blobs = {}
  cursor = 36
  [0, 2, 0x10000].each_with_index do |expected, index|
    slot, start = signature.unpack("N2", offset: 12 + index * 8)
    raise "invalid signature index/layout" unless slot == expected && start == cursor && start + 8 <= length
    _kind, size = signature.unpack("N2", offset: start)
    raise "invalid signature blob bounds" unless size >= 8 && start + size <= length
    blobs[slot] = [start, signature.byteslice(start, size)]
    cursor += size
  end
  raise "unsupported requirements or CMS signature" unless cursor == length &&
    blobs[2][1] == [0xfade0c01, 12, 0].pack("N3") && blobs[0x10000][1] == [0xfade0b01, 8].pack("N2")
  directory = MachO::CodeSigning::CodeDirectory.new(blobs[0][1])
  raise "unsupported ad-hoc CodeDirectory" unless directory.version == 0x20400 && directory.flags == 2 &&
    directory.hash_type == 2 && directory.hash_size == 32 && directory.platform == 0 && directory.page_size == 14 &&
    directory.scatter_offset == 0 && directory.team_offset == 0 && directory.code_limit64 == 0 &&
    directory.n_special_slots == 2 && directory.code_limit == offset && directory.n_code_slots == (offset + 16383) / 16384 &&
    directory.hash_offset + directory.n_code_slots * 32 == directory.length
  hash_start = directory.hash_offset
  special = directory.serialize.byteslice(hash_start - 64, 64)
  raise "invalid special-slot hashes" unless special == Digest::SHA256.digest(blobs[2][1]) + ("\x00" * 32)
  directory.n_code_slots.times do |index|
    expected = Digest::SHA256.digest(bytes.byteslice(index * 16384, [16384, offset - index * 16384].min))
    raise "invalid original code-slot hash" unless directory.serialize.byteslice(hash_start + index * 32, 32) == expected
  end
  { offset: offset, allocation: allocation, signature: signature,
    hashes: offset + blobs[0][0] + hash_start, count: directory.n_code_slots }
end


def rehash_preserved(macho, bytes, layout)
  commands = macho[:LC_CODE_SIGNATURE]
  result = macho.serialize.dup
  raise "signature layout changed during relocation" unless commands.size == 1 &&
    [commands.first.dataoff, commands.first.datasize] == layout.values_at(:offset, :allocation) &&
    result.bytesize == bytes.bytesize && result.byteslice(layout[:offset], layout[:allocation]) == layout[:signature]
  layout[:count].times do |index|
    page = result.byteslice(index * 16384, [16384, layout[:offset] - index * 16384].min)
    result[layout[:hashes] + index * 32, 32] = Digest::SHA256.digest(page)
  end
  result
end


def normalize(target, dylib_id = nil, signature_policy = nil)
  raise "Ruby >= 3.3 required" if (RUBY_VERSION.split('.').map(&:to_i) <=> [3, 3]) == -1
  source = audit_library # Do not load captured code before its complete audit.
  raise "unknown signature policy" unless [nil, "preserve-adhoc-layout", "asan-arm64-rpath"].include?(signature_policy)
  raise "one explicit target required" unless target
  raise "noncanonical target path" if target.split('/').include?('..')
  path = File.expand_path(target)
  raise "target must be under project build/" unless path.start_with?("#{ROOT}/build/")
  current = ROOT
  Pathname.new(path.delete_prefix("#{ROOT}/")).each_filename do |part|
    current = File.join(current, part)
    raise "symlink target component" if File.lstat(current).symlink?
  end
  bytes, stat = regular(path, 512 << 20)
  raise "target must belong to current user" unless stat.uid == Process.euid
  asan = signature_policy == "asan-arm64-rpath"
  extracted = false
  if asan
    raise "invalid ASan policy target/ID" unless path.end_with?("/#{ASAN_PATH}") && dylib_id == ASAN_ID
    original = sha(bytes) == ASAN_SOURCE_SHA && bytes.bytesize == ASAN_SOURCE_BYTES
    normalized = sha(bytes) == ASAN_RESULT_SHA && bytes.bytesize == ASAN_RESULT_BYTES
    raise "ASan source/result SHA or size mismatch" unless original || normalized
  end
  require "#{ROOT}/references/ruby-macho/lib/macho"
  if asan && original
    raise "ASan input must be FAT" unless [0xcafebabe, 0xcafebabf].include?(bytes.unpack1("N"))
    fat = MachO::FatFile.new_from_bin(bytes.dup)
    raise "exactly one ARM64 ASan slice required" unless fat.machos.count { |m| m.cputype == :arm64 } == 1
    macho = fat.extract(:arm64)
    extracted = true
  else
    macho = MachO::MachOFile.new_from_bin(bytes.dup)
  end
  header = macho.serialize.unpack("L<4")
  raise "thin ARM64 executable/dylib required" unless header[0] == 0xfeedfacf && header[1] == 0x100000c && [2, 6].include?(header[3])
  if dylib_id
    # Explicit observed policy, not a universal Homebrew/Rust install-name rule.
    canonical = dylib_id.match?(%r{\A/opt/homebrew/opt/[A-Za-z0-9@+_.-]+(?:/[A-Za-z0-9@+_.-]+)*/[A-Za-z0-9_+.-]+\.dylib\z}) &&
                dylib_id.split('/').none? { |part| [".", ".."].include?(part) }
    raise "invalid explicit dylib ID" unless (canonical || asan) && header[3] == 6 && macho.dylib_id &&
                                            File.basename(dylib_id) == File.basename(macho.dylib_id)
  end
  preserved = preserved_adhoc(macho, bytes) if signature_policy == "preserve-adhoc-layout"
  replacements = 0
  relocate = lambda do |name|
    pairs = [["@@HOMEBREW_CELLAR@@", "/opt/homebrew/Cellar"], ["@@HOMEBREW_PREFIX@@", "/opt/homebrew"]]
    pair = pairs.find { |old, _new| name.start_with?(old) }
    pair ? name.sub(*pair) : name
  end
  if macho.dylib_id && (name = dylib_id || relocate.call(macho.dylib_id)) != macho.dylib_id
    macho.change_dylib_id(name)
    replacements += 1
  end
  [[:linked_dylibs, :change_install_name], [:rpaths, :change_rpath]].each do |reader, writer|
    macho.public_send(reader).each do |old|
      next if old.match?(/^@(loader_|executable_|r)path/)
      name = relocate.call(old)
      next if old == name
      macho.public_send(writer, old, name)
      replacements += 1
    end
  end
  modified = replacements.positive? || extracted
  if modified
    if preserved
      result = rehash_preserved(macho, bytes, preserved)
    else
      macho.codesign!(identifier: MachO::CodeSigning.identifier(macho, File.basename(path)))
      result = macho.serialize
    end
    Tempfile.create([".p6-normalize-", ".tmp"], File.dirname(path)) do |temp|
      temp.binmode
      temp.write(result)
      temp.chmod(stat.mode & 0o777)
      temp.flush
      now = File.lstat(path)
      raise "target changed during normalization" unless [now.dev, now.ino, now.nlink, now.size, now.mtime, now.ctime] == [stat.dev, stat.ino, stat.nlink, stat.size, stat.mtime, stat.ctime] && sha(File.binread(path)) == sha(bytes)
      File.rename(temp.path, path)
    end
  else
    result = bytes
  end
  { status: "PASS", scope: "host-tool-data-normalization-only", modified: modified, replacements: replacements,
    before_sha256: sha(bytes), after_sha256: sha(result), source_commit: source.fetch("commit") }
end

begin
  args, options = ARGV.dup, {}
  while args.first&.start_with?("--")
    flag, value = args.shift, args.shift
    raise "invalid or duplicate option" unless %w[--dylib-id --signature-policy].include?(flag) && value && !options.key?(flag)
    options[flag] = value
  end
  raise "one explicit target required" unless args.size == 1
  puts JSON.generate(normalize(args.first, options["--dylib-id"], options["--signature-policy"]))
rescue StandardError => error
  puts JSON.generate(status: "FAIL", scope: "host-tool-data-normalization-only", diagnostic: error.message)
  exit 1
end
