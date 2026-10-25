#nullable enable

using System.IO;

namespace Puae6;

public sealed unsafe partial class Puae6Core
{
	// Only the initial movie LOAD may use this projected identity. Normal state loads remain strict.
	MovieStart? _movieStart;
	internal byte[] InitialNvramHash => _nvramInitialHash;
	internal StateIdentity MovieIdentity(byte[] hash) => MakeIdentity(_parameters.Firmware, hash);

	internal void CheckMovieBoot()
	{
		if (_started || _faulted || _movieStart is not null) throw new InvalidOperationException("a movie starts in a fresh core");
	}

	internal StateIdentity MediaMovieIdentity(byte[]? saveRam)
	{
		var canonical = Puae6MediaIdentity.CanonicalSaveRam(saveRam, _media, _hardFiles, _syncSettings.FloppyWrites);
		return MakeIdentity(_parameters.Firmware, null, canonical is null ? null : StateEnvelope.Sha256(canonical));
	}

	internal void CheckMediaMovieBoot()
	{
		if (_imported is not null) throw new MovieRejectedException(MovieRejection.BadStart, "a movie requires the original media bases before Attach");
	}

	/// <summary>The immutable bases required by an initial movie state, without subsequent guest writes.</summary>
	public byte[]? CloneMovieStateSaveRam() => _imported is null ? null : (byte[])_imported.Bytes.Clone();

	internal void ValidateMediaMovieState(byte[] state, StateIdentity identity)
	{
		// Preflight only. StoreSaveRam installs these bases before the normal strict load.
		using var reader = new BinaryReader(new MemoryStream(state, false));
		var payload = StateEnvelope.Read(reader, identity);
		using var fields = new BinaryReader(new MemoryStream(payload, false));
		Parse(fields);
	}

	internal IDisposable PrepareMovieState(byte[] state, StateIdentity identity, byte[] nvramHash)
	{
		using var stream = new MemoryStream(state, false);
		using var reader = new BinaryReader(stream);
		var payload = StateEnvelope.Read(reader, identity);
		using var body = new MemoryStream(payload, false);
		using var fields = new BinaryReader(body);
		Parse(fields); // Fail before Attach, even for a checksummed envelope with invalid wrapper fields.
		return _movieStart = new MovieStart(this, identity, nvramHash);
	}

	sealed class MovieStart(Puae6Core core, StateIdentity identity, byte[] nvramHash) : IDisposable
	{
		internal readonly StateIdentity Identity = identity;
		internal readonly byte[] NvramHash = nvramHash;
		public void Dispose()
		{
			if (ReferenceEquals(core._movieStart, this)) core._movieStart = null;
		}
	}
}
