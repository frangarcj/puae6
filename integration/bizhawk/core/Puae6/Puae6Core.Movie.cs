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
