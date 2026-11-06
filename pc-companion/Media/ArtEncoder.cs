using System.Security.Cryptography;
using Windows.Foundation;
using Windows.Graphics.Imaging;
using Windows.Storage.Streams;

namespace PcCompanion.Media;

/// <summary>Scales an image to fit a box and re-encodes it as JPEG for the deck (album art, Polarity posters).</summary>
public static class ArtEncoder
{
    public static async Task<AlbumArt> EncodeAsync(IRandomAccessStream input, int maxWidth, int maxHeight, float quality = 0.85f)
    {
        var decoder = await BitmapDecoder.CreateAsync(input);

        var scale = Math.Min(1.0, Math.Min((double)maxWidth / decoder.PixelWidth, (double)maxHeight / decoder.PixelHeight));
        var transform = new BitmapTransform
        {
            ScaledWidth = (uint)Math.Max(1, Math.Round(decoder.PixelWidth * scale)),
            ScaledHeight = (uint)Math.Max(1, Math.Round(decoder.PixelHeight * scale)),
            InterpolationMode = BitmapInterpolationMode.Fant,
        };
        var pixels = await decoder.GetPixelDataAsync(
            BitmapPixelFormat.Bgra8, BitmapAlphaMode.Ignore, transform,
            ExifOrientationMode.RespectExifOrientation, ColorManagementMode.DoNotColorManage);

        using var output = new InMemoryRandomAccessStream();
        var options = new BitmapPropertySet
        {
            ["ImageQuality"] = new BitmapTypedValue(quality, PropertyType.Single),
        };
        var encoder = await BitmapEncoder.CreateAsync(BitmapEncoder.JpegEncoderId, output, options);
        encoder.SetPixelData(
            BitmapPixelFormat.Bgra8, BitmapAlphaMode.Ignore,
            transform.ScaledWidth, transform.ScaledHeight,
            decoder.DpiX, decoder.DpiY, pixels.DetachPixelData());
        await encoder.FlushAsync();

        var data = new byte[output.Size];
        using (var reader = new DataReader(output.GetInputStreamAt(0)))
        {
            await reader.LoadAsync((uint)output.Size);
            reader.ReadBytes(data);
        }

        var hash = Convert.ToHexString(SHA1.HashData(data))[..16].ToLowerInvariant();
        return new AlbumArt(hash, "image/jpeg", data);
    }

    public static async Task<AlbumArt> EncodeAsync(byte[] image, int maxWidth, int maxHeight, float quality = 0.85f)
    {
        using var stream = new InMemoryRandomAccessStream();
        using (var writer = new DataWriter(stream.GetOutputStreamAt(0)))
        {
            writer.WriteBytes(image);
            await writer.StoreAsync();
            await writer.FlushAsync();
            writer.DetachStream();
        }
        stream.Seek(0);
        return await EncodeAsync(stream, maxWidth, maxHeight, quality);
    }
}
