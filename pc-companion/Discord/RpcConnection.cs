using System.Collections.Concurrent;
using System.IO.Pipes;
using System.Text;
using System.Text.Json;

namespace PcCompanion.Discord;

/// <summary>A command answered by Discord with evt "ERROR".</summary>
public sealed class RpcException(string message) : Exception(message);

/// <summary>
/// Discord's local RPC over the named pipe <c>discord-ipc-N</c>. Frames are [opcode int32 LE][length int32 LE][UTF-8 JSON].
/// Commands carry a nonce and are answered with the same nonce; events arrive as cmd "DISPATCH".
/// </summary>
internal sealed class RpcConnection : IAsyncDisposable
{
    private const int OpHandshake = 0;
    private const int OpFrame = 1;
    private const int OpClose = 2;
    private const int OpPing = 3;
    private const int OpPong = 4;

    private readonly NamedPipeClientStream _pipe;
    private readonly Action<string, JsonElement> _onEvent;
    private readonly SemaphoreSlim _writeLock = new(1, 1);
    private readonly ConcurrentDictionary<string, TaskCompletionSource<JsonElement>> _pending = new();
    private readonly TaskCompletionSource<JsonElement> _ready = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private Task? _readLoop;

    private RpcConnection(NamedPipeClientStream pipe, Action<string, JsonElement> onEvent)
    {
        _pipe = pipe;
        _onEvent = onEvent;
    }

    /// <summary>Completes when Discord closes the pipe (quit, restart) or the connection fails.</summary>
    public Task Completion => _readLoop ?? Task.CompletedTask;

    /// <summary>Connects to the first Discord instance found and does the handshake. Null if Discord isn't running.</summary>
    public static async Task<RpcConnection?> OpenAsync(string clientId, Action<string, JsonElement> onEvent, CancellationToken ct)
    {
        for (var i = 0; i < 10; i++)
        {
            var pipe = new NamedPipeClientStream(".", $"discord-ipc-{i}", PipeDirection.InOut, PipeOptions.Asynchronous);
            try
            {
                await pipe.ConnectAsync(200, ct);
            }
            catch (Exception ex) when (ex is TimeoutException or IOException)
            {
                await pipe.DisposeAsync();
                continue;
            }

            var connection = new RpcConnection(pipe, onEvent);
            connection._readLoop = connection.ReadLoopAsync();
            await connection.WriteAsync(OpHandshake, new { v = 1, client_id = clientId }, ct);
            await connection._ready.Task.WaitAsync(TimeSpan.FromSeconds(10), ct);
            return connection;
        }
        return null;
    }

    /// <summary>Sends a command and returns its "data". Throws <see cref="RpcException"/> if Discord answers with an error.</summary>
    public async Task<JsonElement> RequestAsync(string cmd, object? args, TimeSpan timeout, CancellationToken ct, string? evt = null)
    {
        var nonce = Guid.NewGuid().ToString();
        var reply = new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously);
        _pending[nonce] = reply;
        try
        {
            await WriteAsync(OpFrame, new { cmd, args = args ?? new { }, evt, nonce }, ct);
            return await reply.Task.WaitAsync(timeout, ct);
        }
        finally
        {
            _pending.TryRemove(nonce, out _);
        }
    }

    private Task WriteAsync(int op, object payload, CancellationToken ct) =>
        WriteRawAsync(op, JsonSerializer.SerializeToUtf8Bytes(payload), ct);

    private async Task WriteRawAsync(int op, byte[] json, CancellationToken ct)
    {
        var frame = new byte[8 + json.Length];
        BitConverter.TryWriteBytes(frame.AsSpan(0, 4), op);
        BitConverter.TryWriteBytes(frame.AsSpan(4, 4), json.Length);
        json.CopyTo(frame, 8);
        await _writeLock.WaitAsync(ct);
        try
        {
            await _pipe.WriteAsync(frame, ct);
            await _pipe.FlushAsync(ct);
        }
        finally
        {
            _writeLock.Release();
        }
    }

    private async Task ReadLoopAsync()
    {
        var header = new byte[8];
        try
        {
            while (true)
            {
                await _pipe.ReadExactlyAsync(header);
                var op = BitConverter.ToInt32(header, 0);
                var length = BitConverter.ToInt32(header, 4);
                var body = new byte[length];
                await _pipe.ReadExactlyAsync(body);

                switch (op)
                {
                    case OpPing:
                        await WriteRawAsync(OpPong, body, CancellationToken.None);
                        break;
                    case OpClose:
                        throw new IOException($"Discord closed the connection: {Encoding.UTF8.GetString(body)}");
                    case OpFrame:
                        Dispatch(JsonDocument.Parse(body).RootElement.Clone());
                        break;
                }
            }
        }
        catch (Exception ex) when (ex is IOException or EndOfStreamException or ObjectDisposedException)
        {
            _ready.TrySetException(ex);
            foreach (var pending in _pending.Values)
                pending.TrySetException(ex);
        }
    }

    private void Dispatch(JsonElement message)
    {
        var cmd = message.GetProperty("cmd").GetString();
        var evt = message.TryGetProperty("evt", out var e) && e.ValueKind == JsonValueKind.String ? e.GetString() : null;
        var data = message.TryGetProperty("data", out var d) ? d : default;

        if (cmd == "DISPATCH")
        {
            if (evt == "READY")
                _ready.TrySetResult(data);
            else if (evt is not null)
                _onEvent(evt, data);
            return;
        }

        if (message.TryGetProperty("nonce", out var n) && n.GetString() is { } nonce && _pending.TryGetValue(nonce, out var reply))
        {
            if (evt == "ERROR")
                reply.TrySetException(new RpcException(data.TryGetProperty("message", out var m) ? m.GetString() ?? "error" : "error"));
            else
                reply.TrySetResult(data);
        }
    }

    public async ValueTask DisposeAsync()
    {
        await _pipe.DisposeAsync();
        if (_readLoop is not null)
            await _readLoop;
        _writeLock.Dispose();
    }
}
