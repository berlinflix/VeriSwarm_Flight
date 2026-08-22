[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$CandidateSettings,

    [Parameter(Mandatory = $true)]
    [string]$ActiveSettings,

    [Parameter(Mandatory = $true)]
    [string]$BackupSettings,

    [Parameter(Mandatory = $true)]
    [string]$OperationRecord,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedCandidateSha256
)

$ErrorActionPreference = 'Stop'
$running = @(Get-Process -Name UnrealEditor, UnrealEditor-Cmd -ErrorAction SilentlyContinue)
if ($running.Count -gt 0) {
    throw 'Close every Unreal Editor process before activating CoSys settings.'
}

$candidate = (Resolve-Path -LiteralPath $CandidateSettings).Path
$active = [System.IO.Path]::GetFullPath($ActiveSettings)
$backup = [System.IO.Path]::GetFullPath($BackupSettings)
$record = [System.IO.Path]::GetFullPath($OperationRecord)
$paths = @($candidate, $active, $backup, $record)
if (($paths | ForEach-Object { $_.ToUpperInvariant() } | Sort-Object -Unique).Count -ne 4) {
    throw 'Candidate, active, backup, and operation-record paths must be pairwise distinct.'
}
if (Test-Path -LiteralPath $candidate -PathType Container) {
    throw "candidate settings path is a directory: $candidate"
}
if (Test-Path -LiteralPath $backup) {
    throw "refusing to overwrite settings backup: $backup"
}
if (Test-Path -LiteralPath $record) {
    throw "refusing to overwrite operation record: $record"
}
$candidateHash = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash
if ($candidateHash -ne $ExpectedCandidateSha256.ToUpperInvariant()) {
    throw "candidate settings hash mismatch: $candidateHash"
}
$candidateJson = Get-Content -LiteralPath $candidate -Raw | ConvertFrom-Json
if ($candidateJson.SimMode -ne 'Multirotor' -or -not $candidateJson.EnableRpc) {
    throw 'candidate is not an RPC-enabled Multirotor settings document'
}

$activeParent = Split-Path -Parent $active
$backupParent = Split-Path -Parent $backup
$recordParent = Split-Path -Parent $record
New-Item -ItemType Directory -Path $activeParent, $backupParent, $recordParent -Force | Out-Null
$beforeHash = if (Test-Path -LiteralPath $active -PathType Leaf) {
    (Get-FileHash -LiteralPath $active -Algorithm SHA256).Hash
}
else {
    $null
}
$journal = [ordered]@{
    schema = 'veriswarm.cosys.settings_activation.v1'
    status = 'PENDING'
    captured_at_utc = [DateTime]::UtcNow.ToString('o')
    candidate = $candidate
    candidate_sha256 = $candidateHash
    active = $active
    active_sha256_before = $beforeHash
    active_sha256_after = $null
    backup = if ($beforeHash) { $backup } else { $null }
    backup_sha256 = $null
}
$journal | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $record -Encoding utf8NoBOM

$stage = Join-Path $activeParent ('.veriswarm-disaster-' + [Guid]::NewGuid().ToString('N') + '.tmp')
try {
    Copy-Item -LiteralPath $candidate -Destination $stage
    if ((Get-FileHash -LiteralPath $stage -Algorithm SHA256).Hash -ne $candidateHash) {
        throw 'staged settings hash mismatch'
    }
    if ($beforeHash) {
        [System.IO.File]::Replace($stage, $active, $backup)
    }
    else {
        [System.IO.File]::Move($stage, $active)
    }
    $afterHash = (Get-FileHash -LiteralPath $active -Algorithm SHA256).Hash
    if ($afterHash -ne $candidateHash) {
        throw "active settings hash mismatch after activation: $afterHash"
    }
    $journal.status = 'PASS'
    $journal.active_sha256_after = $afterHash
    if ($beforeHash) {
        $journal.backup_sha256 = (Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash
        if ($journal.backup_sha256 -ne $beforeHash) {
            throw 'backup hash differs from the previous active settings hash'
        }
    }
}
catch {
    $journal.status = 'FAIL'
    $journal.error = $_.Exception.Message
    throw
}
finally {
    if (Test-Path -LiteralPath $stage -PathType Leaf) {
        Remove-Item -LiteralPath $stage -Force
    }
    $journal | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $record -Encoding utf8NoBOM
}
$journal | ConvertTo-Json -Depth 4
