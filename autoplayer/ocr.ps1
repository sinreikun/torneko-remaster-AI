param([Parameter(Mandatory=$true)][string]$ImagePath,[string]$HudImagePath)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
function Await($operation,[Type]$resultType) {
    $method=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
    $task=$method.MakeGenericMethod($resultType).Invoke($null,@($operation))
    $task.GetAwaiter().GetResult()
}
$engines=@([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages)
$language=$engines | Where-Object LanguageTag -Match '^ja' | Select-Object -First 1
if (-not $language) { $language=$engines | Select-Object -First 1 }
if (-not $language) { throw 'No Windows OCR recognition language installed' }
$engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
function RecognizeFile([string]$Path) {
    $file=Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
    $stream=Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    try {
        $decoder=Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap=Await ($decoder.GetSoftwareBitmapAsync([Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,[Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)) ([Windows.Graphics.Imaging.SoftwareBitmap])
        try {
            $result=Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
            $lines=@($result.Lines | ForEach-Object {
                @{text=$_.Text;words=@($_.Words | ForEach-Object { @{text=$_.Text;box=@($_.BoundingRect.X,$_.BoundingRect.Y,$_.BoundingRect.Width,$_.BoundingRect.Height)} })}
            })
            @{lines=$lines;height=$bitmap.PixelHeight}
        } finally { if ($bitmap) { $bitmap.Dispose() } }
    } finally { $stream.Dispose() }
}
$full=RecognizeFile $ImagePath
$lines=@($full.lines);$extra=@{}
if ($HudImagePath) {
    try {
        $top=RecognizeFile $HudImagePath
        if ($top.lines.Count -gt 0) {
            # Use the explicitly isolated strip for its region and keep all
            # original results in full_frame_lines for diagnostic comparison.
            $below=@($full.lines | Where-Object {
                $words=@($_.words)
                $words.Count -gt 0 -and @($words | Where-Object { $_.box[1] -lt $top.height }).Count -eq 0
            })
            $lines=@($top.lines)+$below
            $extra=@{full_frame_lines=@($full.lines);top_strip_lines=@($top.lines);top_strip_height=$top.height}
        }
    } catch { $extra=@{top_strip_error=$_.Exception.Message} }
}
$value=@{engine='windows.media.ocr';language=$language.LanguageTag;lines=@($lines);confidence=$null}
foreach ($key in $extra.Keys) { $value[$key]=$extra[$key] }
$value | ConvertTo-Json -Depth 8 -Compress
