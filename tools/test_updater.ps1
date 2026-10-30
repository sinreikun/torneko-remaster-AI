# Tests the actual updater worker. Only its network adapter is replaced for offline cases.
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseInput([IO.File]::ReadAllText((Join-Path $root 'launcher/updater.ps1')),[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors | Out-String)}
$assignment=$ast.Find({param($node) $node -is [Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -eq '$UpdateWorker'},$true)
$worker=& ([scriptblock]::Create($assignment.Right.Extent.Text))
& $worker ':selftest:' ([hashtable]::Synchronized(@{}))
$network=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Read-Remote'},$true)
$offline=[scriptblock]::Create($worker.ToString().Replace($network.Extent.Text,"function Read-Remote { throw 'Offline test transport' }"))
$base=Join-Path ([IO.Path]::GetTempPath()) ("日本語 O'Brien & update-"+[Guid]::NewGuid().ToString('N'))
try {
    $updates=Join-Path $base 'updates';$null=New-Item -ItemType Directory -Force -Path $updates
    $manifest=Get-Content (Join-Path $root 'release/latest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $dir=Join-Path $updates $manifest.sha256;$null=New-Item -ItemType Directory -Path $dir
    Copy-Item (Join-Path $root ('release/'+$manifest.path)) (Join-Path $dir 'bundle.zip')
    Copy-Item (Join-Path $root 'release/latest.json') (Join-Path $updates 'current.json')
    $data=Join-Path $base 'data';$null=New-Item -ItemType Directory -Path $data
    [IO.File]::WriteAllText((Join-Path $data 'user-data'),'unchanged')
    $state=[hashtable]::Synchronized(@{})
    & $offline $base $state
    if($state.Directory -cne $dir -or $state.Version -cne $manifest.version -or -not $state.Warning){throw 'Offline cached startup failed'}
    Move-Item (Join-Path $updates 'current.json') (Join-Path $updates 'previous.json')
    $state=[hashtable]::Synchronized(@{})
    & $offline $base $state
    if($state.Directory -cne $dir){throw 'Previous verified version fallback failed'}
    [IO.File]::AppendAllText((Join-Path $dir 'bundle.zip'),'corruption')
    $rejected=$false
    try { & $offline $base ([hashtable]::Synchronized(@{})) } catch { $rejected=$true }
    if(-not $rejected){throw 'Corrupt cached version accepted'}
    if([IO.File]::ReadAllText((Join-Path $data 'user-data')) -cne 'unchanged'){throw 'User data modified'}
} finally { if(Test-Path -LiteralPath $base){Remove-Item -LiteralPath $base -Recurse -Force} }
Write-Output 'Updater syntax, ZIP validation, offline/current/previous fallback and data preservation passed.'
