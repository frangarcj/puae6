#!/usr/bin/env ruby
# Normalize bottle data only. Never install or execute the supplied Mach-O.
require "json"
require "digest"
require "pathname"
require "find"
require "tempfile"

ROOT = File.expand_path("../..", __dir__)
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

def normalize(target, dylib_id = nil)
  raise "Ruby >= 3.3 required" if (RUBY_VERSION.split('.').map(&:to_i) <=> [3, 3]) == -1
  source = audit_library # Do not load captured code before its complete audit.
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
  header = bytes.unpack("L<4")
  raise "thin ARM64 executable/dylib required" unless header[0] == 0xfeedfacf && header[1] == 0x100000c && [2, 6].include?(header[3])
  require "#{ROOT}/references/ruby-macho/lib/macho"
  macho = MachO::MachOFile.new_from_bin(bytes.dup)
  if dylib_id
    # Explicit observed policy, not a universal Homebrew/Rust install-name rule.
    canonical = dylib_id.match?(%r{\A/opt/homebrew/opt/[A-Za-z0-9@+_.-]+(?:/[A-Za-z0-9@+_.-]+)*/[A-Za-z0-9_+.-]+\.dylib\z}) &&
                dylib_id.split('/').none? { |part| [".", ".."].include?(part) }
    raise "invalid explicit dylib ID" unless canonical && header[3] == 6 && macho.dylib_id &&
                                            File.basename(dylib_id) == File.basename(macho.dylib_id)
  end
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
  if replacements.positive?
    macho.codesign!(identifier: MachO::CodeSigning.identifier(macho, File.basename(path)))
    result = macho.serialize
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
  { status: "PASS", scope: "host-tool-data-normalization-only", modified: replacements.positive?, replacements: replacements,
    before_sha256: sha(bytes), after_sha256: sha(result), source_commit: source.fetch("commit") }
end

begin
  args = ARGV.dup
  dylib_id = args.first == "--dylib-id" ? (args.shift; args.shift) : nil
  raise "one target with optional --dylib-id ID required" unless args.size == 1 && (ARGV.size == 1 || (ARGV.size == 3 && dylib_id))
  puts JSON.generate(normalize(args.first, dylib_id))
rescue StandardError => error
  puts JSON.generate(status: "FAIL", scope: "host-tool-data-normalization-only", diagnostic: error.message)
  exit 1
end
