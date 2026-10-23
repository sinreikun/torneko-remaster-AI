param([Parameter(Mandatory=$true)][string]$Payload, [switch]$SelfTest, [switch]$UiSmokeTest, [switch]$IntegrationTest, [switch]$DownloadSelfTest, [string]$TestBaseDir)
$ErrorActionPreference = 'Stop'
# A GUI exe can inherit PowerShell 7's module path from its caller. Prefer the
# Windows PowerShell 5.1 system modules; do not depend on caller profiles.
$systemModules = [IO.Path]::Combine($PSHOME, 'Modules')
$env:PSModulePath = $systemModules + [IO.Path]::PathSeparator + $env:PSModulePath
foreach ($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Management','Microsoft.PowerShell.Archive')) {
    Import-Module ([IO.Path]::Combine($systemModules, $module)) -ErrorAction Stop
}

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$BaseDir = Join-Path $env:LOCALAPPDATA 'LocalGamePilot'
if ($IntegrationTest -or $DownloadSelfTest) {
    if (-not $TestBaseDir) { throw 'Integration tests require an isolated TestBaseDir.' }
    $BaseDir = [IO.Path]::GetFullPath($TestBaseDir)
}
$payloadHash = (Get-FileHash -LiteralPath $Payload -Algorithm SHA256).Hash.Substring(0,12)
$AppDir = Join-Path $BaseDir ('app-0.12.1-' + $payloadHash)

# This block runs in a dedicated runspace. UI updates are polled on the UI thread.
$InstallWorker = {
    param($BaseDir, $AppDir, $Payload, $Options, $State)
    $ErrorActionPreference = 'Stop'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    function Check-Cancel {
        if ($State.Cancel) { throw 'キャンセルしました。もう一度開始すると導入済みの部品を再利用します。' }
    }
    function Get-Json($Url, $Body = $null) {
        Check-Cancel
        $request = [Net.HttpWebRequest]::Create($Url)
        $request.UserAgent = 'LocalGamePilot/0.12.1'
        if ($request.Address.IsLoopback) { $request.Proxy = $null }
        $request.Timeout = 30000
        $request.ReadWriteTimeout = 30000
        if ($null -ne $Body) {
            $request.Method = 'POST'; $request.ContentType = 'application/json'
            $bytes = [Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json))
            $request.ContentLength = $bytes.Length
            $bodyStream = $request.GetRequestStream()
            try { $bodyStream.Write($bytes, 0, $bytes.Length) } finally { $bodyStream.Dispose() }
        }
        $response = $request.GetResponse()
        try {
            $reader = New-Object IO.StreamReader($response.GetResponseStream())
            try { return ($reader.ReadToEnd() | ConvertFrom-Json) } finally { $reader.Dispose() }
        } finally { $response.Dispose() }
    }
    function Get-OfficialPage($Url) {
        Check-Cancel
        if (-not $Url.StartsWith('https://github.com/ollama/ollama/releases/')) { throw 'Unsupported release metadata origin' }
        $request = [Net.HttpWebRequest]::Create($Url)
        $request.UserAgent = 'LocalGamePilot/0.12.1'; $request.Timeout=30000; $request.ReadWriteTimeout=30000
        $response = $request.GetResponse()
        try {
            if (-not $response.ResponseUri.AbsoluteUri.StartsWith('https://github.com/ollama/ollama/releases/')) { throw 'Unexpected release metadata redirect' }
            $reader = New-Object IO.StreamReader($response.GetResponseStream())
            try { return @{Text=$reader.ReadToEnd();Uri=$response.ResponseUri.AbsoluteUri} } finally { $reader.Dispose() }
        } finally { $response.Dispose() }
    }
    function Get-OllamaRelease {
        try {
            $release = Get-Json 'https://api.github.com/repos/ollama/ollama/releases/latest'
            $assets = @($release.assets | Where-Object { $_.name -eq 'ollama-windows-amd64.zip' })
            if ($assets.Count -eq 1 -and $assets[0].digest -match '^sha256:[0-9a-fA-F]{64}$') { return $release }
        } catch { $State.Message='公式APIに接続できないため、公式リリースページのSHA-256を確認しています…' }
        # GitHub API has shared anonymous rate limits. The same publisher's HTML
        # exposes its asset digest; never replace verification with an unsigned URL.
        $latest = Get-OfficialPage 'https://github.com/ollama/ollama/releases/latest'
        $match = [regex]::Match($latest.Uri,'^https://github[.]com/ollama/ollama/releases/tag/(v[0-9]+[.][0-9]+[.][0-9]+)$')
        if (-not $match.Success) { throw 'Cannot identify the official stable Ollama release' }
        $tag = $match.Groups[1].Value
        $page = Get-OfficialPage ('https://github.com/ollama/ollama/releases/expanded_assets/' + $tag)
        $relative = '/ollama/ollama/releases/download/' + $tag + '/ollama-windows-amd64.zip'
        $rows = @([regex]::Matches($page.Text,'(?s)<li\b.*?</li>') | Where-Object { $_.Value.Contains('href="' + $relative + '"') })
        if ($rows.Count -ne 1) { throw 'Cannot identify the official Windows archive metadata' }
        $hashes = @([regex]::Matches($rows[0].Value,'sha256:([0-9a-fA-F]{64})') | ForEach-Object { $_.Groups[1].Value.ToLowerInvariant() } | Select-Object -Unique)
        if ($hashes.Count -ne 1) { throw 'Official release page has no unambiguous SHA-256 digest' }
        return [pscustomobject]@{tag_name=$tag;assets=@([pscustomobject]@{name='ollama-windows-amd64.zip';browser_download_url=('https://github.com' + $relative);digest=('sha256:' + $hashes[0])})}
    }
    function Get-FreeSpace($Path) {
        $drive = New-Object IO.DriveInfo([IO.Path]::GetPathRoot($Path))
        return $drive.AvailableFreeSpace
    }
    function Assert-PythonSignature($Path) {
        $signature = Get-AuthenticodeSignature -LiteralPath $Path
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(?:^|,\s*)O=Python Software Foundation(?:,|$)') {
            throw 'Python の署名または発行元を確認できません。未検証ファイルは実行しません。'
        }
    }
    function Download($Url, $Destination, $ExpectedHash = '') {
        if (-not $Url.StartsWith('https://')) { throw 'HTTPS 以外の配布元は使用できません。' }
        $part = $Destination + '.part'
        $metadataFile = $part + '.json'
        if ($ExpectedHash -and (Test-Path $Destination)) {
            if ((Get-FileHash $Destination -Algorithm SHA256).Hash -eq $ExpectedHash) { return }
            Remove-Item -LiteralPath $Destination -Force
        }
        for ($attempt = 1; $attempt -le 3; $attempt++) {
            Check-Cancel
            $response = $null
            try {
                $request = [Net.HttpWebRequest]::Create($Url)
                $request.UserAgent = 'LocalGamePilot/0.12.1'
                $request.Timeout = 30000; $request.ReadWriteTimeout = 30000
                [long]$offset = 0
                if ((Test-Path $part) -and (Test-Path $metadataFile)) {
                    try {
                        $metadata = Get-Content $metadataFile -Raw | ConvertFrom-Json
                        if ($metadata.url -eq $Url -and $metadata.etag) {
                            $offset = (Get-Item $part).Length
                            if ($offset -gt 0) { $request.AddRange($offset); $request.Headers['If-Range'] = $metadata.etag }
                        }
                    } catch { $offset = 0 }
                }
                $response = $request.GetResponse()
                if ($response.ResponseUri.Scheme -ne 'https') { throw 'HTTPS 以外への転送を拒否しました。' }
                $resume = $offset -gt 0 -and [int]$response.StatusCode -eq 206
                if ($resume -and $response.Headers['Content-Range'] -notmatch ('^bytes ' + $offset + '-')) { throw '再開位置が一致しません。' }
                if (-not $resume) { $offset = 0 }
                if ($response.ContentLength -gt 0 -and (Get-FreeSpace $Destination) -lt ($response.ContentLength + 536870912)) { throw 'ダウンロード用の空き容量が不足しています。' }
                @{url=$Url; etag=$response.Headers['ETag']} | ConvertTo-Json | Set-Content -LiteralPath $metadataFile -Encoding UTF8
                $mode = if ($resume) { [IO.FileMode]::Append } else { [IO.FileMode]::Create }
                $downloadStream = $response.GetResponseStream()
                $output = New-Object IO.FileStream($part, $mode, [IO.FileAccess]::Write)
                try {
                    $buffer = New-Object byte[] 262144
                    [long]$total = $offset
                    while (($read = $downloadStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                        Check-Cancel
                        $output.Write($buffer, 0, $read); $total += $read
                        $State.Message = ('取得: {0:N1} MB / {1:N1} MB（試行{2}/3）' -f ($total / 1048576), ([Math]::Max(0,$response.ContentLength + $offset) / 1048576), $attempt)
                    }
                    if ($response.ContentLength -ge 0 -and $total -ne $response.ContentLength + $offset) { throw 'ダウンロードサイズが一致しません。' }
                } finally { $output.Dispose(); $downloadStream.Dispose() }
                if ($ExpectedHash -and (Get-FileHash $part -Algorithm SHA256).Hash -ne $ExpectedHash) {
                    Remove-Item -LiteralPath $part -Force
                    throw '取得したファイルの SHA-256 が一致しません。'
                }
                Move-Item -LiteralPath $part -Destination $Destination -Force
                Remove-Item -LiteralPath $metadataFile -Force -ErrorAction SilentlyContinue
                return
            } catch {
                if ($State.Cancel -or $attempt -eq 3) { throw }
                if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 416) { Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue }
                $State.Message = '通信を再試行しています… ' + $_.Exception.Message
                Start-Sleep -Milliseconds (500 * $attempt)
            } finally { if ($response) { $response.Dispose() } }
        }
    }
    function Run-Process($File, $Arguments, $Label) {
        Check-Cancel
        $State.Message = $Label
        Add-Content -LiteralPath (Join-Path $BaseDir 'setup-trace.log') -Value ((Get-Date -Format o) + ' ' + $Label) -Encoding UTF8
        $log = Join-Path $BaseDir 'install.stdout.log'
        $errlog = Join-Path $BaseDir 'install.stderr.log'
        # Do not kill installers mid-transaction. Cancellation takes effect at the next safe boundary.
        $State.Message = $Label + '（実行中。中止はこの処理の完了後）'
        $p = Start-Process -FilePath $File -ArgumentList $Arguments -Wait -PassThru -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError $errlog
        if ($p.ExitCode -ne 0 -and $p.ExitCode -ne 3010) {
            $detail = ''
            if (Test-Path $errlog) { $detail = Get-Content -LiteralPath $errlog -Raw }
            throw ($Label + ' に失敗しました。終了コード: ' + $p.ExitCode + "`n" + $detail)
        }
        Check-Cancel
    }
    try {
        New-Item -ItemType Directory -Path $BaseDir -Force | Out-Null
        $downloads = Join-Path $BaseDir 'downloads'
        New-Item -ItemType Directory -Path $downloads -Force | Out-Null
        $runtime = Join-Path $BaseDir ('python-' + $Options.Python)
        $python = Join-Path $runtime 'python.exe'
        $pythonw = Join-Path $runtime 'pythonw.exe'
        if (-not (Test-Path $python)) {
            $State.Message = 'Python の公式配布一覧を確認しています…'
            $page = Invoke-WebRequest -Uri 'https://www.python.org/downloads/windows/' -UseBasicParsing -TimeoutSec 30
            $series = [regex]::Escape($Options.Python)
            $pattern = 'https://www\.python\.org/ftp/python/(' + $series + '\.\d+)/python-\1-amd64\.exe'
            $candidates = @([regex]::Matches($page.Content, $pattern) | ForEach-Object {
                [PSCustomObject]@{ Version = [version]$_.Groups[1].Value; Url = $_.Value }
            } | Sort-Object Version -Descending)
            if ($candidates.Count -eq 0) { throw '選択した Python の公式 x64 インストーラーが見つかりません。' }
            $installer = Join-Path $downloads ('python-' + $candidates[0].Version.ToString() + '.exe')
            Download $candidates[0].Url $installer
            $State.Message = 'Python の発行元署名を検証しています…'
            Assert-PythonSignature $installer
            $arguments = '/quiet InstallAllUsers=0 Include_launcher=0 InstallLauncherAllUsers=0 Include_tcltk=1 Include_pip=1 Include_test=0 Include_doc=0 Shortcuts=0 AssociateFiles=0 PrependPath=0 TargetDir="' + $runtime + '"'
            Run-Process $installer $arguments 'Python と画面UIの導入'
            if (-not (Test-Path $pythonw)) { throw 'Python の導入が完了していません。' }
        }
        $State.Message = '画面取得部品を確認しています…'
        # Always verify/import and reconcile the declared dependency. pip verifies TLS and wheel hashes.
        Run-Process $python '-m pip install --disable-pip-version-check "Pillow>=12,<13"' '画面取得部品 Pillow の導入・確認'
        Run-Process $python '-c "import tkinter; from PIL import ImageGrab; print(''ready'')"' 'Python と画面部品の検証'
        Check-Cancel
        if (-not (Test-Path $AppDir)) {
            $State.Message = '本体を展開しています…'
            $staging = $AppDir + '.staging-' + [Guid]::NewGuid().ToString('N')
            New-Item -ItemType Directory -Path $staging | Out-Null
            try {
                Expand-Archive -LiteralPath $Payload -DestinationPath $staging -Force
                if (-not (Test-Path (Join-Path $staging 'autoplayer/core.py'))) { throw '本体アーカイブが不正です。' }
                Move-Item -LiteralPath $staging -Destination $AppDir
            } finally { if (Test-Path $staging) { Remove-Item -LiteralPath $staging -Recurse -Force } }
        }
        $endpoint = $Options.Endpoint.TrimEnd('/')
        if ($Options.Backend -eq 'managed') {
            $endpoint = 'http://127.0.0.1:11435'
            $ollamaDir = Join-Path $BaseDir 'ollama'
            $ollama = Join-Path $ollamaDir 'ollama.exe'
            if (-not (Test-Path $ollama)) {
                $State.Message = 'Ollama の公式配布情報を取得しています…'
                $release = Get-OllamaRelease
                $assets = @($release.assets | Where-Object { $_.name -eq 'ollama-windows-amd64.zip' })
                if ($assets.Count -ne 1) { throw '公式 Ollama x64 アーカイブが見つかりません。' }
                $asset = $assets[0]
                if (-not $asset.browser_download_url.StartsWith('https://github.com/ollama/ollama/releases/download/')) { throw 'Ollama の公式配布元ではありません。' }
                if ($asset.digest -notmatch '^sha256:[0-9a-fA-F]{64}$') { throw '公式配布情報に SHA-256 がないため導入できません。' }
                $archive = Join-Path $downloads 'ollama-windows-amd64.zip'
                Download $asset.browser_download_url $archive $asset.digest.Substring(7)
                $State.Message = 'Ollama の SHA-256 を検証しています…'
                $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
                if ($actual -ne $asset.digest.Substring(7).ToLowerInvariant()) { throw 'Ollama の SHA-256 が一致しません。' }
                Add-Type -AssemblyName System.IO.Compression.FileSystem
                $zip = [IO.Compression.ZipFile]::OpenRead($archive)
                try { [long]$extractedBytes = ($zip.Entries | Measure-Object -Property Length -Sum).Sum } finally { $zip.Dispose() }
                if ((Get-FreeSpace $ollamaDir) -lt ($extractedBytes + 536870912)) { throw 'Ollama の展開用空き容量が不足しています。' }
                $staging = $ollamaDir + '.staging-' + [Guid]::NewGuid().ToString('N')
                try {
                    Expand-Archive -LiteralPath $archive -DestinationPath $staging -Force
                    if (-not (Test-Path (Join-Path $staging 'ollama.exe'))) { throw 'Ollama の実行ファイルがありません。' }
                    Move-Item -LiteralPath $staging -Destination $ollamaDir
                    Remove-Item -LiteralPath $archive -Force
                    $release.tag_name | Set-Content -LiteralPath (Join-Path $ollamaDir 'version.txt') -Encoding UTF8
                } finally { if (Test-Path $staging) { Remove-Item -LiteralPath $staging -Recurse -Force } }
            }
            $running = $false
            try { $null = Get-Json ($endpoint + '/api/version'); $running = $true } catch { }
            if (-not $running) {
                $State.Message = 'ローカル LLM サーバーを起動しています…'
                $oldHost = [Environment]::GetEnvironmentVariable('OLLAMA_HOST', 'Process')
                $oldModels = [Environment]::GetEnvironmentVariable('OLLAMA_MODELS', 'Process')
                try {
                    [Environment]::SetEnvironmentVariable('OLLAMA_HOST', '127.0.0.1:11435', 'Process')
                    [Environment]::SetEnvironmentVariable('OLLAMA_MODELS', (Join-Path $BaseDir 'models'), 'Process')
                    $server = Start-Process -FilePath $ollama -ArgumentList 'serve' -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $BaseDir 'ollama.stdout.log') -RedirectStandardError (Join-Path $BaseDir 'ollama.stderr.log')
                } finally {
                    [Environment]::SetEnvironmentVariable('OLLAMA_HOST', $oldHost, 'Process')
                    [Environment]::SetEnvironmentVariable('OLLAMA_MODELS', $oldModels, 'Process')
                }
                $ready = $false
                for ($i = 0; $i -lt 120; $i++) {
                    Check-Cancel
                    if ($server.HasExited) { throw 'Ollama サーバーが終了しました。' }
                    try { $null = Get-Json ($endpoint + '/api/version'); $ready = $true; break } catch { Start-Sleep -Milliseconds 500 }
                }
                if (-not $ready) { throw 'Ollama の起動を確認できません。' }
            }
        }
        $State.Message = 'モデルを確認しています…'
        $tags = Get-Json ($endpoint + '/api/tags')
        if (-not @($tags.models | Where-Object { $_.name -eq $Options.Model }).Count) {
            $State.Message = 'モデルのダウンロードを開始しています…'
            $request = [Net.HttpWebRequest]::Create($endpoint + '/api/pull')
            $request.Method = 'POST'; $request.ContentType = 'application/json'
            $request.Proxy = $null; $request.Timeout = 30000; $request.ReadWriteTimeout = 120000
            $body = [Text.Encoding]::UTF8.GetBytes((@{ name = $Options.Model; stream = $true } | ConvertTo-Json))
            $request.ContentLength = $body.Length
            $stream = $request.GetRequestStream()
            try { $stream.Write($body, 0, $body.Length) } finally { $stream.Dispose() }
            $response = $request.GetResponse()
            $reader = New-Object IO.StreamReader($response.GetResponseStream())
            $success = $false
            try {
                while ($null -ne ($line = $reader.ReadLine())) {
                    Check-Cancel
                    $progress = $line | ConvertFrom-Json
                    if ($progress.error) { throw $progress.error }
                    $State.Message = ('モデル: {0} {1:N1} / {2:N1} GB' -f $progress.status, ($progress.completed / 1073741824), ($progress.total / 1073741824))
                    if ($progress.status -eq 'success') { $success = $true }
                }
            } finally { $reader.Dispose(); $response.Dispose() }
            if (-not $success) { throw 'モデルの取得完了を確認できません。' }
        }
        $State.Message = 'モデルの画像対応能力を確認しています…'
        $show = Get-Json ($endpoint + '/api/show') @{model = $Options.Model}
        if (-not (@($show.capabilities) -contains 'vision')) { throw '選択モデルの画像対応を確認できません。Ollama の更新が必要な場合があります。' }
        $data = Join-Path $BaseDir 'data'
        New-Item -ItemType Directory -Path $data -Force | Out-Null
        $settingsFile = Join-Path $data 'settings.json'
        $settings = @{}
        if (Test-Path $settingsFile) {
            try {
                $old = Get-Content -LiteralPath $settingsFile -Raw -Encoding UTF8 | ConvertFrom-Json
                foreach ($property in $old.PSObject.Properties) { $settings[$property.Name] = $property.Value }
            } catch { Copy-Item -LiteralPath $settingsFile -Destination ($settingsFile + '.broken-' + [Guid]::NewGuid().ToString('N')) }
        }
        $settings['provider'] = 'ollama'; $settings['endpoint'] = $endpoint; $settings['model'] = $Options.Model
        $json = $settings | ConvertTo-Json -Depth 10
        $utf8 = New-Object Text.UTF8Encoding($false)
        [IO.File]::WriteAllText($settingsFile + '.tmp', $json, $utf8)
        Move-Item -LiteralPath ($settingsFile + '.tmp') -Destination $settingsFile -Force
        [IO.File]::WriteAllText((Join-Path $BaseDir 'bootstrap.json'), ($Options | ConvertTo-Json), $utf8)
        Check-Cancel
        $State.Python = $pythonw; $State.Ready = $true
        $State.Message = '準備完了。本体を起動します。'
    } catch {
        $State.Error = $_.Exception.Message
        try { [IO.File]::WriteAllText((Join-Path $BaseDir 'setup-error.log'), $State.Error) } catch { }
    }
    finally { $State.Done = $true }
}

if ($DownloadSelfTest) {
    try {
    New-Item -ItemType Directory -Path $BaseDir -Force | Out-Null
    $State = @{Cancel=$false;Message=''}
    $definitions = $InstallWorker.Ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]}, $true)
    . ([scriptblock]::Create(($definitions | ForEach-Object {$_.Extent.Text}) -join "`n"))
    $extract = Join-Path $BaseDir 'payload'
    Expand-Archive -LiteralPath $Payload -DestinationPath $extract -Force
    $source = Join-Path $extract 'knowledge/profile.json'
    $expected = (Get-FileHash $source -Algorithm SHA256).Hash
    $url = 'https://github.com/' + $env:GITHUB_REPOSITORY + '/raw/' + $env:GITHUB_SHA + '/knowledge/profile.json'
    $target = Join-Path $BaseDir 'download.json'
    $checks = @()
    Download $url $target $expected
    if ((Get-FileHash $target -Algorithm SHA256).Hash -ne $expected) { throw 'Full download failed' }
    $checks += 'HTTPS download and SHA-256'
    # Recreate the exact persistent state left by an interrupted download.
    $bytes = [IO.File]::ReadAllBytes($source)
    [IO.File]::WriteAllBytes($target + '.part', $bytes[0..16])
    $request = [Net.HttpWebRequest]::Create($url); $request.Method='HEAD'; $request.Timeout=30000
    $response = $request.GetResponse()
    try { $etag = $response.Headers['ETag'] } finally { $response.Dispose() }
    @{url=$url;etag=$etag} | ConvertTo-Json | Set-Content ($target + '.part.json') -Encoding UTF8
    Remove-Item $target
    Download $url $target $expected
    if ((Get-FileHash $target -Algorithm SHA256).Hash -ne $expected) { throw 'Resume/restart integrity failed' }
    $checks += 'Interrupted transfer resumes or safely restarts'
    # A verified cached artifact must not need another network connection.
    Download 'https://unreachable.invalid/cached' $target $expected
    $checks += 'Verified cache works without network'
    $bad = Join-Path $BaseDir 'bad.json'
    $rejected = $false
    try { Download $url $bad ('0' * 64) } catch { $rejected=$true }
    if (-not $rejected -or (Test-Path $bad)) { throw 'Corrupt artifact was accepted' }
    $checks += 'Wrong checksum rejected after bounded retries'
    $State.Cancel=$true; $rejected=$false
    try { Download $url $bad $expected } catch { $rejected=$true }
    $State.Cancel=$false
    if (-not $rejected) { throw 'Cancellation failed' }
    $checks += 'Cancellation stops transfer'
    $rejected=$false
    try { Download 'http://localhost/unsigned' $bad $expected } catch { $rejected=$true }
    if (-not $rejected) { throw 'Plain HTTP was accepted' }
    $checks += 'Plain HTTP rejected'
    $freeSpaceFunction = ${function:Get-FreeSpace}
    try {
        function Get-FreeSpace($Path) { return 0 }
        $rejected=$false
        try { Download $url $bad $expected } catch { $rejected=$_.Exception.Message -like '*空き容量*' }
        if (-not $rejected -or (Test-Path $bad)) { throw 'Insufficient disk space was not handled safely' }
        $checks += 'Insufficient disk space prevents file creation'
    } finally { Set-Item -Path Function:Get-FreeSpace -Value $freeSpaceFunction }
    $ownExe = [IO.Path]::GetFullPath((Join-Path (Split-Path $Payload -Parent) '../dist/LocalGamePilot.exe'))
    if (-not (Test-Path $ownExe)) { throw 'Missing native executable signature test fixture' }
    $rejected=$false
    try { Assert-PythonSignature $ownExe } catch { $rejected=$_.Exception.Message -like '*署名*' }
    if (-not $rejected) { throw 'A non-Python executable passed the publisher guard' }
    $checks += 'Unsigned or wrong-publisher installer rejected'

    $jsonFunction = ${function:Get-Json}
    try {
        function Get-Json($Url,$Body=$null) { throw 'Injected GitHub anonymous API rate limit (403)' }
        $release = Get-OllamaRelease
        $asset = @($release.assets | Where-Object name -eq 'ollama-windows-amd64.zip')[0]
        if ($asset.digest -notmatch '^sha256:[0-9a-f]{64}$' -or -not $asset.browser_download_url.StartsWith('https://github.com/ollama/ollama/releases/download/')) { throw 'Release fallback lost publisher or checksum validation' }
        $checks += 'GitHub API denial falls back to official HTML with SHA-256'
    } finally { Set-Item -Path Function:Get-Json -Value $jsonFunction }

    @{passed=$true;checks=$checks} | ConvertTo-Json | Set-Content (Join-Path $BaseDir 'download-tests.json') -Encoding UTF8
    Write-Output 'Downloader fault and resume tests passed.'
    exit 0
    } catch {
        @{passed=$false;error=$_.Exception.Message;checks=$checks} | ConvertTo-Json | Set-Content (Join-Path $BaseDir 'download-tests.json') -Encoding UTF8
        throw
    }
}

if ($IntegrationTest) {
    $options = @{Python='3.13'; Backend='managed'; Model='qwen2.5vl:3b'; Endpoint='http://127.0.0.1:11435'}
    $state = [hashtable]::Synchronized(@{Done=$false;Cancel=$false;Ready=$false;Error='';Message=''})
    & $InstallWorker $BaseDir $AppDir $Payload $options $state
    if ($state.Error -or -not $state.Ready) { throw ('AUTOMATED INSTALL FAILED: ' + $state.Error) }
    # Repeat the same installer to validate reuse and recovery without touching user configuration.
    $state.Done=$false; $state.Ready=$false
    & $InstallWorker $BaseDir $AppDir $Payload $options $state
    if ($state.Error -or -not $state.Ready) { throw ('REPEAT INSTALL FAILED: ' + $state.Error) }
    $python = Join-Path (Split-Path $state.Python) 'python.exe'
    $report = Join-Path $BaseDir 'integration-report.json'
    Push-Location $AppDir
    try {
        & $python -m autoplayer.runtime_check --endpoint $options.Endpoint.Replace('11434','11435') --model $options.Model --output $report --verify-ocr-components
        if ($LASTEXITCODE) { throw 'APPLICATION / RUNTIME CHECK FAILED' }
    } finally { Pop-Location }
    Write-Output ('INTEGRATION PASSED: ' + $report)
    exit 0
}

if ($SelfTest) {
    # Validate the payload using the same Windows extraction primitive, without downloading or installing.
    $check = Join-Path ([IO.Path]::GetTempPath()) ('GamePilotTest-' + [Guid]::NewGuid().ToString('N'))
    try {
        Expand-Archive -LiteralPath $Payload -DestinationPath $check
        if (-not (Test-Path (Join-Path $check 'autoplayer/core.py'))) { throw 'Missing application payload' }
        $errors = $null; $tokens = $null
        $null = [Management.Automation.Language.Parser]::ParseInput($InstallWorker.ToString(), [ref]$tokens, [ref]$errors)
        if ($errors.Count) { throw ($errors | Out-String) }
        Write-Output 'Bootstrap payload and worker syntax passed.'
    } finally { if (Test-Path $check) { Remove-Item $check -Recurse -Force } }
    exit 0
}

# bootstrap.json is written only after a successful, consented installation.
# Subsequent launches use that installation without re-running installers,
# model downloads, or overwriting the user's current application settings.
$resumeError = ''
$process = $null
if (-not $UiSmokeTest -and (Test-Path (Join-Path $BaseDir 'bootstrap.json'))) {
    try {
        $saved = Get-Content -LiteralPath (Join-Path $BaseDir 'bootstrap.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($saved.Python -notin @('3.14','3.13') -or $saved.Backend -notin @('managed','existing')) { throw '保存された準備情報が不正です。' }
        $pythonw = Join-Path $BaseDir ('python-' + $saved.Python + '/pythonw.exe')
        if (-not (Test-Path $pythonw)) { throw '導入済みの Python が見つかりません。' }
        if (-not (Test-Path (Join-Path $AppDir 'autoplayer/__main__.py'))) {
            $staging = $AppDir + '.staging-' + [Guid]::NewGuid().ToString('N')
            try {
                Expand-Archive -LiteralPath $Payload -DestinationPath $staging -Force
                if (-not (Test-Path (Join-Path $staging 'autoplayer/__main__.py'))) { throw '本体アーカイブが不正です。' }
                if (Test-Path $AppDir) { Remove-Item -LiteralPath $AppDir -Recurse -Force }
                Move-Item -LiteralPath $staging -Destination $AppDir
            } finally { if (Test-Path $staging) { Remove-Item -LiteralPath $staging -Recurse -Force } }
        }
        if ($saved.Backend -eq 'managed') {
            $ollama = Join-Path $BaseDir 'ollama/ollama.exe'
            if (-not (Test-Path $ollama)) { throw '導入済みの Ollama が見つかりません。' }
            $running = $false
            $response = $null
            try {
                $request = [Net.HttpWebRequest]::Create('http://127.0.0.1:11435/api/version')
                $request.Proxy = $null; $request.Timeout = 1000; $request.ReadWriteTimeout = 1000
                $response = $request.GetResponse(); $running = $true
            } catch { } finally { if ($response) { $response.Dispose() } }
            if (-not $running) {
                $serverInfo = New-Object Diagnostics.ProcessStartInfo
                $serverInfo.FileName = $ollama; $serverInfo.Arguments = 'serve'
                $serverInfo.UseShellExecute = $false; $serverInfo.CreateNoWindow = $true
                $serverInfo.EnvironmentVariables['OLLAMA_HOST'] = '127.0.0.1:11435'
                $serverInfo.EnvironmentVariables['OLLAMA_MODELS'] = Join-Path $BaseDir 'models'
                $server = [Diagnostics.Process]::Start($serverInfo)
                $server.Dispose()
            }
        }
        $psi = New-Object Diagnostics.ProcessStartInfo
        $psi.FileName = $pythonw; $psi.Arguments = '-m autoplayer'; $psi.WorkingDirectory = $AppDir
        $psi.UseShellExecute = $false; $psi.EnvironmentVariables['GAMEPILOT_DATA_DIR'] = Join-Path $BaseDir 'data'
        $process = [Diagnostics.Process]::Start($psi)
    } catch { $resumeError = '通常起動できないため準備画面を表示しました: ' + $_.Exception.Message }
    if ($process) {
        $process.WaitForExit()
        $appExitCode = $process.ExitCode; $process.Dispose()
        if ($appExitCode -ne 0) { [Windows.Forms.MessageBox]::Show('本体が異常終了しました。保存先のログを確認してください。','起動エラー') | Out-Null }
        exit $appExitCode
    }
}

$form = New-Object Windows.Forms.Form
$form.Text = 'ゲーム攻略ツール - 初回準備 / 起動'
$form.ClientSize = New-Object Drawing.Size(720, 590)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'; $form.MaximizeBox = $false
$form.Font = New-Object Drawing.Font('Yu Gothic UI', 10)
function Label($text, $x, $y, $width, $height) {
    $control = New-Object Windows.Forms.Label
    $control.Text = $text; $control.Location = New-Object Drawing.Point($x,$y)
    $control.Size = New-Object Drawing.Size($width,$height); $form.Controls.Add($control)
    return $control
}
$null = Label '必要な部品を選択してください。ダウンロード・検証・導入・起動は自動で進みます。' 20 15 680 45
$null = Label 'Python（本体と画面UIのランタイム / PSF License / 約30 MB + 導入領域）' 20 65 680 25
$pythonChoice = New-Object Windows.Forms.ComboBox
$pythonChoice.DropDownStyle = 'DropDownList'; $pythonChoice.Location = New-Object Drawing.Point(20,95)
$pythonChoice.Size = New-Object Drawing.Size(680,28)
$pythonChoice.Items.AddRange(@('Python 3.14 - 新しい標準版（公式の最新x64インストーラー）','Python 3.13 - 互換性を優先（公式の最新x64インストーラー）')); $pythonChoice.SelectedIndex = 0
$form.Controls.Add($pythonChoice)
$null = Label 'LLM実行環境（Ollama: MIT / モデルの利用条件は別）' 20 140 680 25
$backendChoice = New-Object Windows.Forms.ComboBox
$backendChoice.DropDownStyle = 'DropDownList'; $backendChoice.Location = New-Object Drawing.Point(20,170)
$backendChoice.Size = New-Object Drawing.Size(680,28)
$backendChoice.Items.AddRange(@('Ollama を自動導入・起動 - CPU/GPUを自動判定（数百MB〜数GB）','起動済みの Ollama を再利用 - ランタイムのダウンロード不要')); $backendChoice.SelectedIndex = 0
$form.Controls.Add($backendChoice)
$endpointBox = New-Object Windows.Forms.TextBox
$endpointBox.Location = New-Object Drawing.Point(20,205); $endpointBox.Size = New-Object Drawing.Size(680,28)
$endpointBox.Text = 'http://127.0.0.1:11434'; $endpointBox.Enabled = $false; $form.Controls.Add($endpointBox)
$backendChoice.Add_SelectedIndexChanged({ $endpointBox.Enabled = ($backendChoice.SelectedIndex -eq 1) })
$null = Label '画像対応モデル（必要なRAM/VRAMはモデル容量より大きくなります）' 20 245 680 25
$modelChoice = New-Object Windows.Forms.ComboBox
$modelChoice.DropDownStyle = 'DropDownList'; $modelChoice.Location = New-Object Drawing.Point(20,275)
$modelChoice.Size = New-Object Drawing.Size(680,28)
$modelChoice.Items.AddRange(@('qwen2.5vl:3b - 軽量 約3.2GB / Apache-2.0','qwen2.5vl:7b - 推奨 約6GB / Apache-2.0','qwen2.5vl:32b - 大容量 約21GB / Apache-2.0','gemma3:4b - 別系列 約3.3GB / Gemma Terms')); $modelChoice.SelectedIndex = 1
try {
    $ramGB = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1073741824
    if ($ramGB -lt 12) { $modelChoice.SelectedIndex = 0 }
} catch { }
$form.Controls.Add($modelChoice)
if (Test-Path (Join-Path $BaseDir 'bootstrap.json')) {
    try {
        $saved = Get-Content -LiteralPath (Join-Path $BaseDir 'bootstrap.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $index = [Array]::IndexOf(@('3.14','3.13'), $saved.Python)
        if ($index -ge 0) { $pythonChoice.SelectedIndex = $index }
        $index = [Array]::IndexOf(@('managed','existing'), $saved.Backend)
        if ($index -ge 0) { $backendChoice.SelectedIndex = $index }
        $index = [Array]::IndexOf(@('qwen2.5vl:3b','qwen2.5vl:7b','qwen2.5vl:32b','gemma3:4b'), $saved.Model)
        if ($index -ge 0) { $modelChoice.SelectedIndex = $index }
        if ($saved.Endpoint) { $endpointBox.Text = $saved.Endpoint }
    } catch { }
}
$license = New-Object Windows.Forms.LinkLabel
$license.Text = '選択モデルの情報・利用条件'; $license.Location = New-Object Drawing.Point(20,315)
$license.Size = New-Object Drawing.Size(500,25)
$license.Add_LinkClicked({
    $names = @('qwen2.5vl:3b','qwen2.5vl:7b','qwen2.5vl:32b','gemma3:4b')
    Start-Process ('https://ollama.com/library/' + $names[$modelChoice.SelectedIndex])
}); $form.Controls.Add($license)
$consent = New-Object Windows.Forms.CheckBox
$consent.Text = '選択した部品の利用条件を確認し、このPCへの導入に同意します。'
$consent.Location = New-Object Drawing.Point(20,350); $consent.Size = New-Object Drawing.Size(680,30)
$form.Controls.Add($consent)
$null = Label '保存先: %LOCALAPPDATA%\LocalGamePilot（管理者権限・PATHの変更なし）' 20 390 680 30
$status = Label '待機中。導入済みの部品・モデルは再利用します。' 20 430 680 65
if ($resumeError) { $status.Text = $resumeError }
$start = New-Object Windows.Forms.Button
$start.Text = '自動セットアップして起動'; $start.Location = New-Object Drawing.Point(20,510)
$start.Size = New-Object Drawing.Size(460,45); $form.Controls.Add($start)
$cancel = New-Object Windows.Forms.Button
$cancel.Text = '中止 / 閉じる'; $cancel.Location = New-Object Drawing.Point(500,510)
$cancel.Size = New-Object Drawing.Size(200,45); $form.Controls.Add($cancel)
$State = [hashtable]::Synchronized(@{ Done=$false; Cancel=$false; Ready=$false; Error=''; Message='' })
$script:engine = $null; $script:handle = $null
$start.Add_Click({
    if (-not $consent.Checked) { [Windows.Forms.MessageBox]::Show('利用条件を確認して同意してください。') | Out-Null; return }
    $endpoint = $endpointBox.Text.Trim()
    $uri = $null
    if (-not [Uri]::TryCreate($endpoint, [UriKind]::Absolute, [ref]$uri) -or $uri.Scheme -ne 'http' -or $uri.Host -notin @('127.0.0.1','localhost','[::1]') -or $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $uri.AbsolutePath -ne '/') {
        [Windows.Forms.MessageBox]::Show('接続先は http://127.0.0.1:11434 のようなローカルURLを指定してください。') | Out-Null; return
    }
    $Options = @{
        Python = @('3.14','3.13')[$pythonChoice.SelectedIndex]
        Backend = @('managed','existing')[$backendChoice.SelectedIndex]
        Model = @('qwen2.5vl:3b','qwen2.5vl:7b','qwen2.5vl:32b','gemma3:4b')[$modelChoice.SelectedIndex]
        Endpoint = $endpoint
    }
    $State.Done=$false; $State.Ready=$false; $State.Cancel=$false; $State.Error=''
    $start.Enabled=$false; $pythonChoice.Enabled=$false; $backendChoice.Enabled=$false; $modelChoice.Enabled=$false; $endpointBox.Enabled=$false
    $script:engine = [PowerShell]::Create()
    $null = $script:engine.AddScript($InstallWorker.ToString()).AddArgument($BaseDir).AddArgument($AppDir).AddArgument($Payload).AddArgument($Options).AddArgument($State)
    $script:handle = $script:engine.BeginInvoke()
})
$cancel.Add_Click({ if ($start.Enabled) { $form.Close() } else { $State.Cancel=$true; $status.Text='中止しています。実行中の導入処理が完了するまでお待ちください。' } })
$form.Add_FormClosing({ param($sender,$e) if (-not $start.Enabled) { $State.Cancel=$true; $e.Cancel=$true } })
$timer = New-Object Windows.Forms.Timer
$timer.Interval = 200
$timer.Add_Tick({
    if ($UiSmokeTest -and $start.Enabled) { $form.Close(); return }
    if (-not $start.Enabled) { $status.Text = $State.Message }
    if ($State.Done) {
        $State.Done=$false
        try { $null = $script:engine.EndInvoke($script:handle) } catch { $State.Error=$_.Exception.Message }
        if ($script:engine.Streams.Error.Count -and -not $State.Error) { $State.Error=($script:engine.Streams.Error | Out-String) }
        $script:engine.Dispose(); $script:engine=$null
        $start.Enabled=$true; $pythonChoice.Enabled=$true; $backendChoice.Enabled=$true; $modelChoice.Enabled=$true; $endpointBox.Enabled=($backendChoice.SelectedIndex -eq 1)
        if ($State.Error) { $status.Text=$State.Error; [Windows.Forms.MessageBox]::Show($State.Error,'セットアップ停止') | Out-Null }
        elseif ($State.Ready -and -not $State.Cancel) {
            $psi = New-Object Diagnostics.ProcessStartInfo
            $psi.FileName=$State.Python; $psi.Arguments='-m autoplayer'; $psi.WorkingDirectory=$AppDir
            $psi.UseShellExecute=$false; $psi.EnvironmentVariables['GAMEPILOT_DATA_DIR']=Join-Path $BaseDir 'data'
            $process=[Diagnostics.Process]::Start($psi)
            $form.Hide(); $timer.Stop()
            # The native launcher retains a mutex until the application exits.
            $process.WaitForExit()
            if ($process.ExitCode -ne 0) { [Windows.Forms.MessageBox]::Show('本体が異常終了しました。保存先のログを確認してください。','起動エラー') | Out-Null }
            $form.Close()
        }
    }
})
$timer.Start()
try { $null = $form.ShowDialog() } finally { $timer.Dispose(); if ($script:engine) { $script:engine.Dispose() }; $form.Dispose() }
