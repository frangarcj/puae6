"""Reference encoder and validator of the native state envelope of docs/adr/0009-state-format.md.

It exists before the native serializer (T15) so that the format is something that can be run and attacked: T15's C++ must give the same
bytes for the vectors in tests/state/vectors/envelope-v1.json and the same verdict for every defect tests/state/test_envelope.py makes.
Nothing here knows the emulator; chunk bodies are opaque bytes.
"""
from dataclasses import dataclass
import hashlib
import struct

MAGIC = b"P6NATIVE"
WATERBOX_MAGIC = b"P6WATERB"
FORMAT_VERSION = 1
HEADER_SIZE = 336
PROFILE_A500 = 1
CORRUPT_STATE, INCOMPATIBLE = 9, 10          # p6_result codes of bridge/include/p6.h

# One chunk per subsystem of manifests/state-inventory.json, in this order, each exactly once.
TAGS = (b"P6CP", b"P6SC", b"P6RM", b"P6RT", b"P6CH", b"P6VD", b"P6AU", b"P6CI", b"P6DK", b"P6IN", b"P6BR")
SUPPORTED_CHUNK_VERSION = 1
CHUNK_HEADER = struct.Struct("<4sIQ")

# Header layout: (name, offset, size). The ADR's table, in code.
FIELDS = (("magic", 0, 8), ("format_version", 8, 4), ("header_size", 12, 4), ("profile_id", 16, 4), ("flags", 20, 4), ("unit_index", 24, 8),
          ("master_ticks", 32, 8), ("payload_length", 40, 8), ("chunk_count", 48, 4), ("reserved0", 52, 4), ("core_build_id", 56, 32),
          ("upstream_commit", 88, 20), ("reserved1", 108, 4), ("patch_series_hash", 112, 32), ("codegen_hash", 144, 32), ("config_hash", 176, 32),
          ("media_manifest_hash", 208, 32), ("firmware_sha256", 240, 32), ("payload_sha256", 272, 32), ("header_sha256", 304, 32))
IDENTITY_FIELDS = ("profile_id", "core_build_id", "upstream_commit", "patch_series_hash", "codegen_hash", "config_hash", "media_manifest_hash",
                   "firmware_sha256")


class StateError(Exception):
    """A state that is refused. `code` is the p6_result the native loader returns, `field` names what was wrong."""

    def __init__(self, code, field, message):
        super().__init__(f"{field}: {message}")
        self.code, self.field = code, field


def domain_hash(domain, *parts):
    """SHA-256 over the domain name, a zero byte and the parts: two domains never give the same hash for the same bytes."""
    digest = hashlib.sha256(domain.encode("ascii") + b"\0")
    for part in parts:
        digest.update(part)
    return digest.digest()


def config_hash(machine, video_standard, audio_rate_hz, flags, random_seed, rtc_epoch_seconds):
    return domain_hash("p6/config/v1", struct.pack("<IIIIQq", machine, video_standard, audio_rate_hz, flags, random_seed, rtc_epoch_seconds))


def config_hash_audio(base, separation, interpolation, filter_, filter_type):
    """T73: the hash of a config whose Paula output differs from the profile's: derived from the hash of the flags word (`base`, 32 bytes) and the four resolved settings in upstream's own
    values (separation 0..10, sound_interpol, sound_filter, sound_filter_type; the type is 0 while the filter is off). The profile's setting keeps `base` (bridge/state.cpp)."""
    return domain_hash("p6/config/audio/v1", base, bytes([separation, interpolation, filter_, filter_type]))


def config_hash_cpu(base, model, multiplier=0):
    """T52: the hash of a config whose CPU or clock is not the machine's own: derived from the hash so far (`base`, 32 bytes: the flags word's, then the audio word's when there is one),
    the resolved model as 0 = 68000, 1 = 68010, 2 = 68020 and the multiplier of the clock (0 while it is the machine's own). The machine's own CPU and clock keep `base` (bridge/state.cpp)."""
    return domain_hash("p6/config/cpu/v1", base, bytes([model, multiplier]))


def media_manifest_hash(media):
    """`media` is a list of (type, content_id) in the order of the p6_load array."""
    return domain_hash("p6/media-manifest/v1", struct.pack("<I", len(media)), *(struct.pack("<I", kind) + content_id for kind, content_id in media))


def core_build_id(upstream_commit, patch_series_hash, codegen_hash, sources_hash, profile_id=PROFILE_A500):
    return domain_hash("p6/core-build/v1", struct.pack("<I", FORMAT_VERSION), upstream_commit, patch_series_hash, codegen_hash, sources_hash,
                       struct.pack("<I", profile_id))


@dataclass(frozen=True)
class Identity:
    profile_id: int
    core_build_id: bytes
    upstream_commit: bytes
    patch_series_hash: bytes
    codegen_hash: bytes
    config_hash: bytes
    media_manifest_hash: bytes
    firmware_sha256: bytes


@dataclass(frozen=True)
class State:
    unit_index: int
    master_ticks: int
    identity: Identity
    chunks: dict            # tag -> (version, body)


def encode(identity, unit_index, master_ticks, chunks):
    """`chunks` maps every tag of TAGS to (version, body). Returns the whole state: header then payload."""
    if list(chunks) != [t for t in TAGS if t in chunks] or set(chunks) != set(TAGS):
        raise ValueError("a native state carries every chunk once, in the order of TAGS")
    payload = b"".join(CHUNK_HEADER.pack(tag, chunks[tag][0], len(chunks[tag][1])) + chunks[tag][1] for tag in TAGS)
    header = bytearray(HEADER_SIZE)
    struct.pack_into("<8sIIII", header, 0, MAGIC, FORMAT_VERSION, HEADER_SIZE, identity.profile_id, 0)
    struct.pack_into("<QQQII", header, 24, unit_index, master_ticks, len(payload), len(TAGS), 0)
    for name in ("core_build_id", "upstream_commit", "patch_series_hash", "codegen_hash", "config_hash", "media_manifest_hash", "firmware_sha256"):
        _, offset, size = next(f for f in FIELDS if f[0] == name)
        value = getattr(identity, name)
        if len(value) != size:
            raise ValueError(f"{name} is {size} bytes")
        header[offset:offset + size] = value
    header[272:304] = domain_hash("p6/native-payload/v1", payload)
    header[304:336] = domain_hash("p6/native-header/v1", bytes(header[:304]))
    return bytes(header) + payload


def decode(data, expected, bound, chunk_bounds=None):
    """The validation order of the ADR. Returns a State or raises StateError; nothing is allocated from a length before it has been checked."""
    chunk_bounds = chunk_bounds or {}
    if len(data) < 8:
        raise StateError(CORRUPT_STATE, "length", "shorter than a magic")
    if data[:8] == WATERBOX_MAGIC:
        raise StateError(INCOMPATIBLE, "magic", "a Waterbox snapshot is not a native state")
    if len(data) < HEADER_SIZE:
        raise StateError(CORRUPT_STATE, "length", "shorter than the header")
    magic, version, header_size, profile_id, flags = struct.unpack_from("<8sIIII", data, 0)
    if magic != MAGIC:
        raise StateError(CORRUPT_STATE, "magic", "not a native state")
    if version != FORMAT_VERSION:
        raise StateError(INCOMPATIBLE, "format_version", f"version {version} is not {FORMAT_VERSION}")
    if header_size != HEADER_SIZE:
        raise StateError(CORRUPT_STATE, "header_size", f"{header_size} is not {HEADER_SIZE}")
    unit_index, master_ticks, payload_length, chunk_count, reserved0 = struct.unpack_from("<QQQII", data, 24)
    reserved1 = struct.unpack_from("<I", data, 108)[0]
    if flags:
        raise StateError(CORRUPT_STATE, "flags", "reserved bits are set")
    if reserved0 or reserved1:
        raise StateError(CORRUPT_STATE, "reserved", "reserved fields are not zero")
    if domain_hash("p6/native-header/v1", data[:304]) != data[304:336]:
        raise StateError(CORRUPT_STATE, "header_sha256", "the header does not match its checksum")
    if payload_length > bound or HEADER_SIZE + payload_length > (1 << 64) - 1 or len(data) != HEADER_SIZE + payload_length:
        raise StateError(CORRUPT_STATE, "payload_length", f"{payload_length} does not fit {len(data) - HEADER_SIZE} bytes of payload or the bound {bound}")
    found = {name: data[offset:offset + size] for name, offset, size in FIELDS if name in IDENTITY_FIELDS and name != "profile_id"}
    found["profile_id"] = profile_id
    for name in IDENTITY_FIELDS:
        if found[name] != getattr(expected, name):
            raise StateError(INCOMPATIBLE, name, "differs from the loaded machine")
    payload = data[HEADER_SIZE:]
    if domain_hash("p6/native-payload/v1", payload) != data[272:304]:
        raise StateError(CORRUPT_STATE, "payload_sha256", "the payload does not match its checksum")
    if chunk_count != len(TAGS):
        raise StateError(CORRUPT_STATE, "chunk_count", f"{chunk_count} chunks, {len(TAGS)} expected")
    chunks, at = {}, 0
    for tag in TAGS:
        if len(payload) - at < CHUNK_HEADER.size:
            raise StateError(CORRUPT_STATE, "chunk", f"{tag.decode()} has no header")
        seen, chunk_version, length = CHUNK_HEADER.unpack_from(payload, at)
        if seen != tag:
            raise StateError(CORRUPT_STATE, "chunk", f"{seen!r} where {tag.decode()} belongs: unknown, repeated or out of order")
        if chunk_version != SUPPORTED_CHUNK_VERSION:
            raise StateError(INCOMPATIBLE, "chunk_version", f"{tag.decode()} version {chunk_version}")
        at += CHUNK_HEADER.size
        if length > len(payload) - at or length > chunk_bounds.get(tag, bound):
            raise StateError(CORRUPT_STATE, "chunk_length", f"{tag.decode()} claims {length} bytes")
        chunks[tag] = (chunk_version, payload[at:at + length])
        at += length
    if at != len(payload):
        raise StateError(CORRUPT_STATE, "payload", "bytes after the last chunk")
    return State(unit_index, master_ticks, Identity(profile_id, found["core_build_id"], found["upstream_commit"], found["patch_series_hash"],
                                                    found["codegen_hash"], found["config_hash"], found["media_manifest_hash"], found["firmware_sha256"]), chunks)
