param([switch]$SelfTest)
$ErrorActionPreference = 'Stop'
$env:PSModulePath = [IO.Path]::Combine($PSHOME,'Modules') + [IO.Path]::PathSeparator + $env:PSModulePath
Import-Module Microsoft.PowerShell.Utility
Import-Module Microsoft.PowerShell.Management
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
# This script is the stable launcher protocol. Application releases do not change it.
$UpdateWorker = {
    param($Base,$State)
    $ErrorActionPreference='Stop'
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
    function Validate-Manifest($m) {
        if ($m.protocol -ne 1 -or $m.version -notmatch '^\d+\.\d+\.\d+$' -or $m.sha256 -notmatch '^[a-f0-9]{64}$' -or $m.bytes -lt 1 -or $m.bytes -gt 16777216) { throw '更新情報の形式が不正です。' }
        if ($m.path -cne ('apps/'+$m.sha256+'.zip')) { throw '更新先が不正です。' }
    }
    function Read-Remote($Url,$Destination,$Limit) {
        $request=[Net.HttpWebRequest]::Create($Url)
        $request.UserAgent='LocalGamePilot-Launcher/1';$request.Timeout=8000;$request.ReadWriteTimeout=8000
        $request.AllowAutoRedirect=$false
        $response=$request.GetResponse()
        try {
            if ([int]$response.StatusCode -ne 200) { throw '更新サーバーが正常に応答しませんでした。' }
            $inputStream=$response.GetResponseStream();$output=[IO.File]::Create($Destination)
            try {
                $buffer=New-Object byte[] 32768;$total=0;$watch=[Diagnostics.Stopwatch]::StartNew()
                while (($count=$inputStream.Read($buffer,0,$buffer.Length)) -gt 0) {
                    $total+=$count
                    if ($total -gt $Limit -or $watch.Elapsed.TotalSeconds -gt 60 -or $State.Cancel) { throw '更新の受信を中断しました。' }
                    $output.Write($buffer,0,$count)
                }
            } finally { $output.Dispose();$inputStream.Dispose() }
        } finally { $response.Dispose() }
    }
    function Verify-Package($Dir,$m) {
        Validate-Manifest $m
        $zip=Join-Path $Dir 'bundle.zip'
        if (-not (Test-Path -LiteralPath $zip)) { throw '保存済み更新がありません。' }
        if ((Get-Item -LiteralPath $zip).Length -ne $m.bytes -or (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant() -cne $m.sha256) { throw '更新ファイルの検証に失敗しました。' }
        $archive=[IO.Compression.ZipFile]::OpenRead($zip)
        try {
            if ($archive.Entries.Count -ne 2) { throw '不正な更新ZIPです。' }
            foreach ($name in @('bootstrap.ps1','application.zip')) {
                $entries=@($archive.Entries | Where-Object { $_.FullName -ceq $name })
                if ($entries.Count -ne 1 -or $entries[0].Length -gt 33554432) { throw '不正な更新内容です。' }
                # Re-extract verified bytes; never trust a previously extracted script.
                [IO.Compression.ZipFileExtensions]::ExtractToFile($entries[0],(Join-Path $Dir $name),$true)
            }
        } finally { $archive.Dispose() }
        return $Dir
    }
    if ($Base -eq ':selftest:') {
        Validate-Manifest ([pscustomobject]@{protocol=1;version='1.2.3';sha256=('a'*64);bytes=12;path=('apps/'+('a'*64)+'.zip')})
        $rejected=$false
        try { Validate-Manifest ([pscustomobject]@{protocol=1;version='1.2.3';sha256=('a'*64);bytes=12;path='../bad.zip'}) } catch { $rejected=$true }
        if (-not $rejected) { throw 'Unsafe manifest accepted' }
        $test=Join-Path ([IO.Path]::GetTempPath()) ('GamePilotUpdate-'+[Guid]::NewGuid().ToString('N'))
        $null=New-Item -ItemType Directory -Path $test
        try {
            $source=Join-Path $test 'source';$null=New-Item -ItemType Directory -Path $source
            [IO.File]::WriteAllText((Join-Path $source 'bootstrap.ps1'),'verified script')
            [IO.File]::WriteAllText((Join-Path $source 'application.zip'),'verified payload')
            $zip=Join-Path $test 'bundle.zip'
            [IO.Compression.ZipFile]::CreateFromDirectory($source,$zip)
            $hash=(Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
            $m=[pscustomobject]@{protocol=1;version='1.2.3';sha256=$hash;bytes=(Get-Item $zip).Length;path=('apps/'+$hash+'.zip')}
            $null=Verify-Package $test $m
            [IO.File]::WriteAllText((Join-Path $test 'bootstrap.ps1'),'tampered extraction')
            $null=Verify-Package $test $m
            if ([IO.File]::ReadAllText((Join-Path $test 'bootstrap.ps1')) -cne 'verified script') { throw 'Cached extraction not repaired' }
            [IO.File]::AppendAllText($zip,'corruption')
            $rejected=$false;try { $null=Verify-Package $test $m } catch { $rejected=$true }
            if (-not $rejected) { throw 'Corrupt bundle accepted' }
            Remove-Item -LiteralPath $zip
            [IO.File]::WriteAllText((Join-Path $source 'unexpected.ps1'),'unexpected')
            [IO.Compression.ZipFile]::CreateFromDirectory($source,$zip)
            $m.sha256=(Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant();$m.path='apps/'+$m.sha256+'.zip';$m.bytes=(Get-Item $zip).Length
            $rejected=$false;try { $null=Verify-Package $test $m } catch { $rejected=$true }
            if (-not $rejected) { throw 'Unexpected archive entry accepted' }
        } finally { Remove-Item -LiteralPath $test -Recurse -Force }
        return
    }
    $updates=Join-Path $Base 'updates';$null=New-Item -ItemType Directory -Force -Path $updates
    $index=Join-Path $updates 'current.json';$temporary=Join-Path $updates 'latest.part'
    $origin='https://raw.githubusercontent.com/sinreikun/torneko-remaster-AI/main/release/'
    try {
        $State.Message='最新版を確認しています…'
        Read-Remote ($origin+'latest.json') $temporary 16384
        $manifest=Get-Content -LiteralPath $temporary -Raw -Encoding UTF8 | ConvertFrom-Json
        Validate-Manifest $manifest
        $dir=Join-Path $updates $manifest.sha256;$null=New-Item -ItemType Directory -Force -Path $dir
        $bundle=Join-Path $dir 'bundle.zip'
        try { $null=Verify-Package $dir $manifest } catch {
            $State.Message='本体 v'+$manifest.version+' を保存しています…'
            $part=Join-Path $dir 'download.part'
            try {
                Read-Remote ($origin+$manifest.path) $part $manifest.bytes
                if ((Get-Item -LiteralPath $part).Length -ne $manifest.bytes -or (Get-FileHash -LiteralPath $part -Algorithm SHA256).Hash.ToLowerInvariant() -cne $manifest.sha256) { throw '更新データが一致しません。' }
                Move-Item -LiteralPath $part -Destination $bundle -Force
            } finally { if (Test-Path -LiteralPath $part) { Remove-Item -LiteralPath $part -Force } }
            $null=Verify-Package $dir $manifest
        }
        # Atomically replace the cache index only after verification and extraction.
        if (Test-Path -LiteralPath $index) {
            $old=$null;try { $old=Get-Content -LiteralPath $index -Raw -Encoding UTF8 | ConvertFrom-Json } catch { }
            if ($old -and $old.sha256 -ceq $manifest.sha256) { Remove-Item -LiteralPath $temporary -Force }
            else { [IO.File]::Replace($temporary,$index,(Join-Path $updates 'previous.json')) }
        }
        else { [IO.File]::Move($temporary,$index) }
        $State.Directory=$dir;$State.Version=$manifest.version
    } catch {
        $State.Warning=$_.Exception.Message
        # Both indexes reference hash-verified complete bundles. No unverified fallback.
        foreach ($saved in @($index,(Join-Path $updates 'previous.json'))) {
            try {
                $manifest=Get-Content -LiteralPath $saved -Raw -Encoding UTF8 | ConvertFrom-Json
                $dir=Verify-Package (Join-Path $updates $manifest.sha256) $manifest
                $State.Directory=$dir;$State.Version=$manifest.version;break
            } catch { }
        }
        if (-not $State.Directory) { throw ('更新を取得できず、保存済みの本体もありません。ネット接続を確認して再起動してください。 '+$State.Warning) }
    } finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force } }
}
if ($SelfTest) {
    $tokens=$null;$errors=$null
    $null=[Management.Automation.Language.Parser]::ParseInput($UpdateWorker.ToString(),[ref]$tokens,[ref]$errors)
    if ($errors.Count) { throw ($errors | Out-String) }
    & $UpdateWorker ':selftest:' ([hashtable]::Synchronized(@{}))
    exit 0
}
$base=Join-Path $env:LOCALAPPDATA 'LocalGamePilot'
$state=[hashtable]::Synchronized(@{Message='更新を確認しています…';Cancel=$false})
$form=New-Object Windows.Forms.Form
$form.Text='ゲーム攻略くん — 起動・自動更新';$form.ClientSize=New-Object Drawing.Size(520,105)
$form.StartPosition='CenterScreen';$form.FormBorderStyle='FixedDialog';$form.MaximizeBox=$false
$label=New-Object Windows.Forms.Label;$label.Location=New-Object Drawing.Point(16,16);$label.Size=New-Object Drawing.Size(490,65);$form.Controls.Add($label)
$engine=[PowerShell]::Create();$null=$engine.AddScript($UpdateWorker.ToString()).AddArgument($base).AddArgument($state);$handle=$engine.BeginInvoke()
$timer=New-Object Windows.Forms.Timer;$timer.Interval=100
$timer.Add_Tick({$label.Text=$state.Message;if($handle.IsCompleted){$form.Close()}})
$form.Add_FormClosing({if(-not $handle.IsCompleted){$state.Cancel=$true}})
$timer.Start()
try { $null=$form.ShowDialog();if($state.Cancel){exit 0};$null=$engine.EndInvoke($handle);if($engine.Streams.Error.Count){throw ($engine.Streams.Error | Out-String)} }
catch { [Windows.Forms.MessageBox]::Show($_.Exception.Message,'起動できませんでした') | Out-Null;exit 1 }
finally { $timer.Dispose();$form.Dispose();$engine.Dispose() }
if ($state.Warning) { $null=New-Item -ItemType Directory -Force -Path $base;[IO.File]::WriteAllText((Join-Path $base 'update-warning.log'),([DateTime]::Now.ToString('s')+' '+$state.Warning)) }
# Run the versioned bootstrap in a child: its exit statements cannot terminate the updater midway.
function Quote-PS($text) { return $text.Replace("'","''") }
$script=Quote-PS (Join-Path $state.Directory 'bootstrap.ps1');$payload=Quote-PS (Join-Path $state.Directory 'application.zip')
$command="& ([scriptblock]::Create([IO.File]::ReadAllText('$script'))) -Payload '$payload'"
$encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
$process=Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList @('-NoLogo','-NoProfile','-NonInteractive','-STA','-EncodedCommand',$encoded) -WindowStyle Hidden -PassThru
$process.WaitForExit();exit $process.ExitCode
