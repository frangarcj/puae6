using System.IO.Compression;
using System.Text;

/// <summary>A BK2 file as BizHawk lays it out: a zip with Header.txt ("Key Value" per line) and Input Log.txt ("[Input]", the LogKey line, one "|...|" line per frame, "[/Input]").</summary>
internal static class MovieFile
{
	internal sealed record Movie(Dictionary<string, string> Header, string LogKey, List<string> Entries);

	internal static void Write(string path, IReadOnlyDictionary<string, string> header, string logKey, IReadOnlyList<string> entries)
	{
		if (File.Exists(path)) File.Delete(path);
		using var zip = ZipFile.Open(path, ZipArchiveMode.Create);
		void Add(string name, string text)
		{
			var entry = zip.CreateEntry(name);
			using var writer = new StreamWriter(entry.Open(), new UTF8Encoding(false));
			writer.Write(text);
		}
		var head = new StringBuilder();
		foreach (var (key, value) in header) head.Append(key).Append(' ').Append(value).Append('\n');
		Add("Header.txt", head.ToString());
		var log = new StringBuilder("[Input]\nLogKey:").Append(logKey).Append('\n');
		foreach (var entry in entries) log.Append(entry).Append('\n');
		log.Append("[/Input]\n");
		Add("Input Log.txt", log.ToString());
	}

	internal static Movie Read(string path)
	{
		using var zip = ZipFile.OpenRead(path);
		string Text(string name)
		{
			var entry = zip.GetEntry(name) ?? throw new InvalidDataException($"{path} has no {name}");
			using var reader = new StreamReader(entry.Open(), Encoding.UTF8);
			return reader.ReadToEnd();
		}
		var header = new Dictionary<string, string>();
		foreach (var line in Text("Header.txt").Split('\n', StringSplitOptions.RemoveEmptyEntries))
		{
			int space = line.IndexOf(' ');
			header[space < 0 ? line : line[..space]] = space < 0 ? "" : line[(space + 1)..];
		}
		string logKey = "";
		var entries = new List<string>();
		foreach (var line in Text("Input Log.txt").Split('\n', StringSplitOptions.RemoveEmptyEntries))
		{
			if (line.StartsWith("LogKey:")) logKey = line["LogKey:".Length..];
			else if (line.StartsWith('|')) entries.Add(line);
		}
		return new Movie(header, logKey, entries);
	}
}
