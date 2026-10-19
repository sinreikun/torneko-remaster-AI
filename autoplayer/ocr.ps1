param([Parameter(Mandatory=$true)][string]$ImagePath)
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
$file=Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
$stream=Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
try {
    $decoder=Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap=Await ($decoder.GetSoftwareBitmapAsync([Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,[Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)) ([Windows.Graphics.Imaging.SoftwareBitmap])
    try {
        $engines=@([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages)
        $language=$engines | Where-Object LanguageTag -Match '^ja' | Select-Object -First 1
        if (-not $language) { $language=$engines | Select-Object -First 1 }
        if (-not $language) { throw 'No Windows OCR recognition language installed' }
        $engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
        $result=Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        $lines=@($result.Lines | ForEach-Object {
            @{text=$_.Text; words=@($_.Words | ForEach-Object { @{text=$_.Text;box=@($_.BoundingRect.X,$_.BoundingRect.Y,$_.BoundingRect.Width,$_.BoundingRect.Height)} })}
        })
        @{engine='windows.media.ocr';language=$language.LanguageTag;lines=$lines;confidence=$null} | ConvertTo-Json -Depth 8 -Compress
    } finally { if ($bitmap) { $bitmap.Dispose() } }
} finally { $stream.Dispose() }
