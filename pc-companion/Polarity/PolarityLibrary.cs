using System.Text.Json;

namespace PcCompanion.Polarity;

/// <summary>A title with watch progress (Polarity's Continue Watching).</summary>
public sealed record PolarityProgress(
    string MetaId,
    string Title,
    string? Subtitle,
    string? PosterUrl,
    string? BackdropUrl,
    double PositionS,
    double DurationS,
    long UpdatedAt)
{
    public double Fraction => DurationS > 0 ? Math.Clamp(PositionS / DurationS, 0, 1) : 0;
}

/// <summary>A title in the library (watchlist).</summary>
public sealed record PolarityTitle(string Id, string Title, string? PosterUrl, long ChangedAt);

public sealed record PolarityData(IReadOnlyList<PolarityProgress> Progress, IReadOnlyList<PolarityTitle> Library, double WatchedThreshold)
{
    public static PolarityData Empty { get; } = new([], [], 0.92);
}

/// <summary>
/// Reads Polarity's <c>polarity.json</c> (Tauri app data folder), read-only and only what the deck shows: titles,
/// posters and progress of the active space. Settings, add-ons, keys and the stream each progress entry played
/// (which can carry tokens) are never read into anything that leaves this class.
/// </summary>
public static class PolarityLibrary
{
    public static string DataFile { get; } = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "app.getpolar.desktop", "polarity.json");

    public static PolarityData Read(string path)
    {
        // Polarity may be writing it; share everything and let a half-written file fail to parse (retried later).
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
        using var doc = JsonDocument.Parse(stream);
        return Parse(doc.RootElement);
    }

    public static PolarityData Parse(JsonElement root)
    {
        var threshold = 0.92;
        if (root.TryGetProperty("settings", out var settings)
            && settings.TryGetProperty("watchedThreshold", out var t) && t.TryGetDouble(out var percent) && percent > 0)
            threshold = percent > 1 ? percent / 100 : percent;

        var space = ActiveSpace(root);
        if (space is not { } s)
            return PolarityData.Empty with { WatchedThreshold = threshold };

        var progress = new List<PolarityProgress>();
        if (s.TryGetProperty("progress", out var entries) && entries.ValueKind == JsonValueKind.Object)
        {
            foreach (var entry in entries.EnumerateObject())
            {
                var p = entry.Value;
                var name = Str(p, "name");
                if (string.IsNullOrWhiteSpace(name))
                    continue;
                progress.Add(new PolarityProgress(
                    MetaId: Str(p, "metaId") ?? entry.Name,
                    Title: name,
                    Subtitle: EpisodeLabel(p),
                    PosterUrl: ImageUrl(p, "poster"),
                    BackdropUrl: ImageUrl(p, "background"),
                    PositionS: Num(p, "position"),
                    DurationS: Num(p, "duration"),
                    UpdatedAt: (long)Num(p, "updatedAt")));
            }
        }

        var library = new List<PolarityTitle>();
        if (s.TryGetProperty("library", out var items) && items.ValueKind == JsonValueKind.Object)
        {
            foreach (var item in items.EnumerateObject())
            {
                var i = item.Value;
                var name = Str(i, "name");
                if (string.IsNullOrWhiteSpace(name))
                    continue;
                var changed = Math.Max((long)Num(i, "addedAt"), (long)Num(i, "updatedAt"));
                library.Add(new PolarityTitle(Str(i, "id") ?? item.Name, name, ImageUrl(i, "poster"), changed));
            }
        }
        return new PolarityData(progress, library, threshold);
    }

    /// <summary>Continue Watching like Polarity's: started (&gt; 30 s), not finished, one entry per title, newest first.</summary>
    public static List<PolarityProgress> ContinueWatching(PolarityData data, int limit = 20) => data.Progress
        .Where(p => p.PositionS > 30 && p.Fraction < data.WatchedThreshold)
        .GroupBy(p => p.MetaId)
        .Select(g => g.MaxBy(p => p.UpdatedAt)!)
        .OrderByDescending(p => p.UpdatedAt)
        .Take(limit)
        .ToList();

    public static List<PolarityTitle> RecentLibrary(PolarityData data, int limit = 30) =>
        data.Library.OrderByDescending(t => t.ChangedAt).Take(limit).ToList();

    private static JsonElement? ActiveSpace(JsonElement root)
    {
        if (!root.TryGetProperty("spaces", out var spaces) || spaces.ValueKind != JsonValueKind.Object)
            return null;
        var active = root.TryGetProperty("activeSpace", out var a) && a.ValueKind == JsonValueKind.String ? a.GetString() : null;
        if (active is not null && spaces.TryGetProperty(active, out var space))
            return space;
        return spaces.TryGetProperty("local", out var local) ? local : null;
    }

    private static string? EpisodeLabel(JsonElement p)
    {
        var season = Num(p, "season");
        var episode = Num(p, "episode");
        if (episode <= 0)
            return null;
        var label = season > 0 ? $"S{season:0} E{episode:0}" : $"Episode {episode:0}";
        var title = Str(p, "episodeTitle");
        return string.IsNullOrWhiteSpace(title) ? label : $"{label} · {title}";
    }

    /// <summary>Only plain http(s) image links; anything else is ignored.</summary>
    private static string? ImageUrl(JsonElement e, string name) =>
        Str(e, name) is { } url && Uri.TryCreate(url, UriKind.Absolute, out var uri)
        && (uri.Scheme == Uri.UriSchemeHttps || uri.Scheme == Uri.UriSchemeHttp) ? url : null;

    private static string? Str(JsonElement e, string name) =>
        e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;

    private static double Num(JsonElement e, string name) =>
        e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number && v.TryGetDouble(out var d) ? d : 0;
}
