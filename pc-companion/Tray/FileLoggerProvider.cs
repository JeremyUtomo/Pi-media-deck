using System.Text;
using Microsoft.Extensions.Logging;

namespace PcCompanion.Tray;

/// <summary>Minimal append-only file log; the tray app has no console. Rolls over to .old past 1 MB at startup.</summary>
internal sealed class FileLoggerProvider : ILoggerProvider
{
    private const long MaxBytes = 1024 * 1024;

    private readonly StreamWriter _writer;
    private readonly object _sync = new();

    public FileLoggerProvider(string path)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        if (File.Exists(path) && new FileInfo(path).Length > MaxBytes)
            File.Move(path, path + ".old", overwrite: true);
        // UTF-8 with BOM (written only when the file is new) so Notepad and Windows PowerShell read it correctly.
        _writer = new StreamWriter(new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.ReadWrite), new UTF8Encoding(true))
        {
            AutoFlush = true,
        };
    }

    public ILogger CreateLogger(string categoryName) => new FileLogger(this, categoryName[(categoryName.LastIndexOf('.') + 1)..]);

    private void Write(string line)
    {
        lock (_sync)
            _writer.WriteLine(line);
    }

    public void Dispose()
    {
        lock (_sync)
            _writer.Dispose();
    }

    private sealed class FileLogger(FileLoggerProvider provider, string category) : ILogger
    {
        public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;

        public bool IsEnabled(LogLevel logLevel) => logLevel >= LogLevel.Information;

        public void Log<TState>(LogLevel logLevel, EventId eventId, TState state, Exception? exception, Func<TState, Exception?, string> formatter)
        {
            if (!IsEnabled(logLevel))
                return;
            var line = $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {logLevel,-11} {category}: {formatter(state, exception)}";
            provider.Write(exception is null ? line : line + Environment.NewLine + exception);
        }
    }
}
