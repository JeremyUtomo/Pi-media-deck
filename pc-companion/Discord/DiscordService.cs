using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Extensions.Logging;

namespace PcCompanion.Discord;

/// <summary>Discord voice state for the deck. Status: not_running, needs_setup, authorizing, denied, ready.</summary>
public sealed record DiscordState(string Status, bool InCall, string? Channel, bool Muted, bool Deafened)
{
    public static DiscordState With(string status) => new(status, false, null, false, false);
}

/// <summary>
/// Follows the local Discord client's voice state (in a call? which channel? muted? deafened?) over RPC and toggles
/// mute/deafen. Discord's RPC needs an OAuth token for the user's *own* Discord application (developer portal):
/// %LOCALAPPDATA%\PiMediaDeck\discord.json = {"clientId": "...", "clientSecret": "..."} is written by the user; the
/// first connect shows an "Authorize" prompt in Discord, after which the token is kept in discord-token.json and refreshed.
/// </summary>
public sealed class DiscordService : IDisposable
{
    private static readonly string[] Scopes = ["rpc", "rpc.voice.read", "rpc.voice.write"];
    private static readonly TimeSpan RetryDelay = TimeSpan.FromSeconds(5);
    private static readonly TimeSpan DeclinedBackoff = TimeSpan.FromMinutes(10);
    private static readonly TimeSpan RequestTimeout = TimeSpan.FromSeconds(10);

    private readonly string _appFile;
    private readonly string _tokenFile;
    private readonly ILogger _log;
    private readonly HttpClient _http = new() { BaseAddress = new Uri("https://discord.com/api/v10/") };
    private readonly CancellationTokenSource _stop = new();
    private readonly object _sync = new();
    private RpcConnection? _connection;
    private DateTimeOffset _authorizeBlockedUntil;

    public DiscordService(string dataDirectory, ILogger<DiscordService> log)
    {
        _appFile = Path.Combine(dataDirectory, "discord.json");
        _tokenFile = Path.Combine(dataDirectory, "discord-token.json");
        _log = log;
    }

    public event Action<DiscordState>? StateChanged;

    public DiscordState Current { get; private set; } = DiscordState.With("not_running");

    public void Start() => _ = RunAsync(_stop.Token);

    public Task ToggleMuteAsync() => SetVoiceAsync(new { mute = !Current.Muted });

    public Task ToggleDeafenAsync() => SetVoiceAsync(new { deaf = !Current.Deafened });

    private async Task SetVoiceAsync(object settings)
    {
        var connection = _connection;
        if (connection is null || Current.Status != "ready")
            return;
        var data = await connection.RequestAsync("SET_VOICE_SETTINGS", settings, RequestTimeout, _stop.Token);
        ApplyVoiceSettings(data); // the reply carries the resulting settings
    }

    private async Task RunAsync(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            try
            {
                var app = LoadJson<AppConfig>(_appFile);
                if (app is null || string.IsNullOrWhiteSpace(app.ClientId) || string.IsNullOrWhiteSpace(app.ClientSecret))
                {
                    Publish(DiscordState.With("needs_setup"));
                    await Task.Delay(TimeSpan.FromSeconds(30), ct);
                    continue;
                }

                await using var connection = await RpcConnection.OpenAsync(app.ClientId, OnEvent, ct);
                if (connection is null)
                {
                    Publish(DiscordState.With("not_running"));
                    await Task.Delay(RetryDelay, ct);
                    continue;
                }

                await AuthenticateAsync(connection, app, ct);
                _connection = connection;
                await connection.RequestAsync("SUBSCRIBE", new { }, RequestTimeout, ct, evt: "VOICE_SETTINGS_UPDATE");
                await connection.RequestAsync("SUBSCRIBE", new { }, RequestTimeout, ct, evt: "VOICE_CHANNEL_SELECT");
                ApplyVoiceSettings(await connection.RequestAsync("GET_VOICE_SETTINGS", null, RequestTimeout, ct), ready: true);
                await RefreshChannelAsync(connection, ct);
                _log.LogInformation("Discord connected");

                await connection.Completion;
                _log.LogInformation("Discord disconnected");
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                return;
            }
            catch (Exception ex)
            {
                _log.LogWarning("Discord: {Error}", ex.Message);
            }
            finally
            {
                _connection = null;
            }

            if (Current.Status is not ("denied" or "needs_setup"))
                Publish(DiscordState.With("not_running"));
            try
            {
                await Task.Delay(RetryDelay, ct);
            }
            catch (OperationCanceledException)
            {
                return;
            }
        }
    }

    private async Task AuthenticateAsync(RpcConnection connection, AppConfig app, CancellationToken ct)
    {
        var token = LoadJson<TokenFile>(_tokenFile);
        if (token is not null && token.ExpiresAt < DateTimeOffset.UtcNow.AddHours(1))
            token = await TryRefreshAsync(app, token, ct);

        if (token is not null)
        {
            try
            {
                await connection.RequestAsync("AUTHENTICATE", new { access_token = token.AccessToken }, RequestTimeout, ct);
                return;
            }
            catch (RpcException ex)
            {
                _log.LogInformation("Discord: saved token rejected ({Error}); asking again", ex.Message);
            }
        }

        if (DateTimeOffset.UtcNow < _authorizeBlockedUntil)
            throw new InvalidOperationException("authorization was declined; not asking again yet");

        Publish(DiscordState.With("authorizing"));
        string code;
        try
        {
            // Shows the "Authorize" prompt in the Discord client; waits for the user's answer.
            var data = await connection.RequestAsync("AUTHORIZE", new { client_id = app.ClientId, scopes = Scopes },
                TimeSpan.FromMinutes(5), ct);
            code = data.GetProperty("code").GetString()!;
        }
        catch (Exception ex) when (ex is RpcException or TimeoutException)
        {
            _authorizeBlockedUntil = DateTimeOffset.UtcNow + DeclinedBackoff;
            Publish(DiscordState.With("denied"));
            throw;
        }

        token = await ExchangeAsync(app, new()
        {
            ["grant_type"] = "authorization_code",
            ["code"] = code,
            ["redirect_uri"] = app.RedirectUri ?? "http://localhost",
        }, ct);
        await connection.RequestAsync("AUTHENTICATE", new { access_token = token.AccessToken }, RequestTimeout, ct);
    }

    private async Task<TokenFile?> TryRefreshAsync(AppConfig app, TokenFile token, CancellationToken ct)
    {
        try
        {
            return await ExchangeAsync(app, new() { ["grant_type"] = "refresh_token", ["refresh_token"] = token.RefreshToken }, ct);
        }
        catch (HttpRequestException ex)
        {
            _log.LogInformation("Discord: token refresh failed ({Error})", ex.Message);
            return null;
        }
    }

    private async Task<TokenFile> ExchangeAsync(AppConfig app, Dictionary<string, string> form, CancellationToken ct)
    {
        form["client_id"] = app.ClientId;
        form["client_secret"] = app.ClientSecret;
        using var response = await _http.PostAsync("oauth2/token", new FormUrlEncodedContent(form), ct);
        response.EnsureSuccessStatusCode();
        var reply = await response.Content.ReadFromJsonAsync<TokenReply>(ct)
            ?? throw new HttpRequestException("empty token reply");
        var token = new TokenFile(reply.AccessToken, reply.RefreshToken, DateTimeOffset.UtcNow.AddSeconds(reply.ExpiresIn));
        File.WriteAllText(_tokenFile, JsonSerializer.Serialize(token));
        return token;
    }

    private void OnEvent(string evt, JsonElement data)
    {
        switch (evt)
        {
            case "VOICE_SETTINGS_UPDATE":
                ApplyVoiceSettings(data);
                break;
            case "VOICE_CHANNEL_SELECT":
                // Look the channel name up off the read loop (a request from inside it would wait on itself).
                if (_connection is { } connection)
                    _ = Task.Run(() => RefreshChannelAsync(connection, _stop.Token));
                break;
        }
    }

    private async Task RefreshChannelAsync(RpcConnection connection, CancellationToken ct)
    {
        try
        {
            var channel = await connection.RequestAsync("GET_SELECTED_VOICE_CHANNEL", null, RequestTimeout, ct);
            var inCall = channel.ValueKind == JsonValueKind.Object;
            var name = inCall && channel.TryGetProperty("name", out var n) ? n.GetString() : null;
            lock (_sync)
                Publish(Current with { InCall = inCall, Channel = string.IsNullOrEmpty(name) ? (inCall ? "Call" : null) : name });
        }
        catch (Exception ex) when (ex is RpcException or IOException or TimeoutException)
        {
            _log.LogWarning("Discord: couldn't read the voice channel: {Error}", ex.Message);
        }
    }

    private void ApplyVoiceSettings(JsonElement data, bool ready = false)
    {
        lock (_sync)
        {
            var state = ready ? Current with { Status = "ready" } : Current;
            if (data.TryGetProperty("mute", out var mute))
                state = state with { Muted = mute.GetBoolean() };
            if (data.TryGetProperty("deaf", out var deaf))
                state = state with { Deafened = deaf.GetBoolean() };
            Publish(state);
        }
    }

    private void Publish(DiscordState state)
    {
        lock (_sync)
        {
            if (state == Current)
                return;
            Current = state;
        }
        StateChanged?.Invoke(state);
    }

    private static T? LoadJson<T>(string path) where T : class
    {
        try
        {
            return File.Exists(path) ? JsonSerializer.Deserialize<T>(File.ReadAllText(path)) : null;
        }
        catch (JsonException)
        {
            return null;
        }
    }

    private sealed record AppConfig(
        [property: JsonPropertyName("clientId")] string ClientId,
        [property: JsonPropertyName("clientSecret")] string ClientSecret,
        [property: JsonPropertyName("redirectUri")] string? RedirectUri);

    private sealed record TokenFile(string AccessToken, string RefreshToken, DateTimeOffset ExpiresAt);

    private sealed record TokenReply(
        [property: JsonPropertyName("access_token")] string AccessToken,
        [property: JsonPropertyName("refresh_token")] string RefreshToken,
        [property: JsonPropertyName("expires_in")] int ExpiresIn);

    public void Dispose()
    {
        _stop.Cancel();
        _http.Dispose();
    }
}
