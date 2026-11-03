using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using PcCompanion.Audio;
using PcCompanion.Discord;
using PcCompanion.Media;
using PcCompanion.Server;
using PcCompanion.Tray;

namespace PcCompanion;

/// <summary>Tray app hosting the deck WebSocket server on :8765 (docs/CONTEXT.md §6).</summary>
internal static class Program
{
    private const int Port = 8765;

    [STAThread]
    private static int Main(string[] args)
    {
        using var instance = new Mutex(initiallyOwned: true, @"Local\PiMediaDeck.PcCompanion", out var isFirstInstance);
        if (!isFirstInstance)
            return 0;

        Application.EnableVisualStyles();
        Application.SetHighDpiMode(HighDpiMode.SystemAware);

        var logPath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PiMediaDeck", "pc-companion.log");
        using var logging = new FileLoggerProvider(logPath);

        // Created on a thread-pool (MTA) thread: Kestrel and the audio poll timer use these COM/WinRT objects
        // from other threads, which would otherwise be marshalled to (and block on) this STA UI thread.
        var (media, audio) = Task.Run(async () => (await MediaService.CreateAsync(), new AudioService())).GetAwaiter().GetResult();
        using var mediaService = media;
        using var audioService = audio;

        var app = BuildServer(args, media, audio, logging, Path.GetDirectoryName(logPath)!);
        var hub = app.Services.GetRequiredService<DeckHub>(); // subscribes to media/audio before any deck connects
        try
        {
            app.StartAsync().GetAwaiter().GetResult();
        }
        catch (Exception ex) when (ex is IOException or InvalidOperationException)
        {
            app.Logger.LogError(ex, "Could not start the server on port {Port}", Port);
            MessageBox.Show($"Could not listen on port {Port}:\n{ex.Message}", "Pi Media Deck", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        audio.Start();
        var discord = app.Services.GetRequiredService<DiscordService>();
        discord.Start();
        app.Logger.LogInformation("Listening on ws://0.0.0.0:{Port}", Port);

        using (var tray = new TrayApp(hub, app.Services.GetRequiredService<ILogger<TrayApp>>(), Port, logPath))
            Application.Run(tray);

        app.Logger.LogInformation("Exiting");
        discord.Dispose();
        app.StopAsync().GetAwaiter().GetResult();
        return 0;
    }

    private static WebApplication BuildServer(string[] args, MediaService media, AudioService audio, ILoggerProvider logging, string dataDirectory)
    {
        var builder = WebApplication.CreateSlimBuilder(args);
        builder.WebHost.ConfigureKestrel(options => options.ListenAnyIP(Port));
        builder.Logging.ClearProviders();
        builder.Logging.AddProvider(logging);
        builder.Logging.AddFilter("Microsoft", LogLevel.Warning);
        builder.Services.AddSingleton(media);
        builder.Services.AddSingleton(audio);
        builder.Services.AddSingleton(sp => new DiscordService(dataDirectory, sp.GetRequiredService<ILogger<DiscordService>>()));
        builder.Services.AddSingleton<DeckHub>();

        var app = builder.Build();
        app.UseWebSockets(new WebSocketOptions { KeepAliveInterval = TimeSpan.FromSeconds(15) });
        app.Map("/", async (HttpContext context, DeckHub hub) =>
        {
            if (!context.WebSockets.IsWebSocketRequest)
            {
                await context.Response.WriteAsync("pc-companion: connect with a WebSocket client.");
                return;
            }

            using var socket = await context.WebSockets.AcceptWebSocketAsync();
            var ip = context.Connection.RemoteIpAddress;
            if (ip is { IsIPv4MappedToIPv6: true })
                ip = ip.MapToIPv4();
            await hub.HandleAsync(socket, $"{ip}:{context.Connection.RemotePort}", context.RequestAborted);
        });
        return app;
    }
}
