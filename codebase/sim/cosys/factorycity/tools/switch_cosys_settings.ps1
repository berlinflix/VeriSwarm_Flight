[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Activate', 'Restore')]
    [string]$Action,

    [Parameter(Mandatory = $true)]
    [string]$CandidateSettingsPath,

    [Parameter(Mandatory = $true)]
    [string]$ActiveSettingsPath,

    [Parameter(Mandatory = $true)]
    [string]$BackupSettingsPath,

    [Parameter(Mandatory = $true)]
    [string]$OperationRecordPath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Fa-f]{64}$')]
    [string]$ExpectedCandidateSha256,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Fa-f]{64}$')]
    [string]$ExpectedPreviousSha256
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Assert-Hash {
    param([string]$Path, [string]$Expected, [string]$Label)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label does not exist: $Path"
    }
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
    if ($actual -ne $Expected.ToUpperInvariant()) {
        throw "$Label hash mismatch: expected $Expected, got $actual"
    }
    return $actual
}

function Resolve-Parent {
    param([string]$Path, [string]$Label)
    $parent = Split-Path -Parent $Path
    if (-not $parent -or -not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw "$Label parent directory does not exist: $parent"
    }
    return (Resolve-Path -LiteralPath $parent).Path
}

function Resolve-CanonicalLeaf {
    param([string]$Path, [string]$Label)
    $fullPath = [IO.Path]::GetFullPath($Path)
    $parent = Resolve-Parent $fullPath $Label
    $leaf = Split-Path -Leaf $fullPath
    if (-not $leaf) {
        throw "$Label must identify a file: $Path"
    }
    $canonical = Join-Path $parent $leaf
    if (Test-Path -LiteralPath $canonical) {
        if (-not (Test-Path -LiteralPath $canonical -PathType Leaf)) {
            throw "$Label must identify a file: $canonical"
        }
        return (Resolve-Path -LiteralPath $canonical).Path
    }
    return $canonical
}

function Write-RecordCreateOnce {
    param([string]$Path, [Collections.IDictionary]$Record)
    $recordText = ($Record | ConvertTo-Json -Depth 5) + "`n"
    $stream = [IO.File]::Open(
        $Path,
        [IO.FileMode]::CreateNew,
        [IO.FileAccess]::Write,
        [IO.FileShare]::None
    )
    try {
        $writer = [IO.StreamWriter]::new($stream, [Text.UTF8Encoding]::new($false))
        try {
            $writer.Write($recordText)
            $writer.Flush()
        }
        finally {
            $writer.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
}

function Write-RecordAtomically {
    param([string]$Path, [Collections.IDictionary]$Record)
    $parent = Resolve-Parent $Path 'Operation record'
    $stage = Join-Path $parent ('.veriswarm-record-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    try {
        $recordText = ($Record | ConvertTo-Json -Depth 5) + "`n"
        [IO.File]::WriteAllText($stage, $recordText, [Text.UTF8Encoding]::new($false))
        [IO.File]::Move($stage, $Path, $true)
    }
    finally {
        if (Test-Path -LiteralPath $stage -PathType Leaf) {
            Remove-Item -LiteralPath $stage -Force
        }
    }
}

function Replace-Atomically {
    param([string]$Source, [string]$Destination)
    $destinationParent = Resolve-Parent $Destination 'Active settings'
    $stage = Join-Path $destinationParent ('.veriswarm-settings-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    try {
        Copy-Item -LiteralPath $Source -Destination $stage
        [IO.File]::Move($stage, $Destination, $true)
    }
    finally {
        if (Test-Path -LiteralPath $stage -PathType Leaf) {
            Remove-Item -LiteralPath $stage -Force
        }
    }
}

$CandidateSettingsPath = Resolve-CanonicalLeaf $CandidateSettingsPath 'Candidate settings'
$ActiveSettingsPath = Resolve-CanonicalLeaf $ActiveSettingsPath 'Active settings'
$BackupSettingsPath = Resolve-CanonicalLeaf $BackupSettingsPath 'Backup settings'
$OperationRecordPath = Resolve-CanonicalLeaf $OperationRecordPath 'Operation record'
$paths = @(
    $CandidateSettingsPath,
    $ActiveSettingsPath,
    $BackupSettingsPath,
    $OperationRecordPath
)
$distinctPaths = [Collections.Generic.HashSet[string]]::new(
    [StringComparer]::OrdinalIgnoreCase
)
foreach ($path in $paths) {
    if (-not $distinctPaths.Add($path)) {
        throw 'Candidate, active, backup, and operation-record paths must be pairwise distinct.'
    }
}

if (Get-Process -Name UnrealEditor, UnrealEditor-Cmd -ErrorAction SilentlyContinue) {
    throw 'Close every Unreal Editor process before changing CoSys settings.'
}
if (Test-Path -LiteralPath $OperationRecordPath) {
    throw "Operation record already exists and will not be overwritten: $OperationRecordPath"
}
Resolve-Parent $OperationRecordPath 'Operation record' | Out-Null

$candidateHash = Assert-Hash $CandidateSettingsPath $ExpectedCandidateSha256 'Candidate settings'
if ($Action -eq 'Activate') {
    $beforeHash = Assert-Hash $ActiveSettingsPath $ExpectedPreviousSha256 'Active settings'
    Resolve-Parent $BackupSettingsPath 'Backup settings' | Out-Null
    if (Test-Path -LiteralPath $BackupSettingsPath) {
        Assert-Hash $BackupSettingsPath $ExpectedPreviousSha256 'Existing settings backup' | Out-Null
    }
    else {
        Copy-Item -LiteralPath $ActiveSettingsPath -Destination $BackupSettingsPath
        Assert-Hash $BackupSettingsPath $ExpectedPreviousSha256 'Created settings backup' | Out-Null
    }
}
else {
    $beforeHash = Assert-Hash $ActiveSettingsPath $ExpectedCandidateSha256 'Active candidate settings'
    Assert-Hash $BackupSettingsPath $ExpectedPreviousSha256 'Settings backup' | Out-Null
}

$preparedRecord = [ordered]@{
    schema = 'veriswarm.factorycity.settings_switch.v1'
    status = 'PREPARED'
    action = $Action
    prepared_at_utc = [DateTime]::UtcNow.ToString('o')
    active_settings_path = $ActiveSettingsPath
    candidate_settings_path = $CandidateSettingsPath
    backup_settings_path = $BackupSettingsPath
    before_sha256 = $beforeHash
    candidate_sha256 = $candidateHash
    previous_sha256 = $ExpectedPreviousSha256.ToUpperInvariant()
}
Write-RecordCreateOnce $OperationRecordPath $preparedRecord

if ($Action -eq 'Activate') {
    Replace-Atomically $CandidateSettingsPath $ActiveSettingsPath
    $afterHash = Assert-Hash $ActiveSettingsPath $ExpectedCandidateSha256 'Activated settings'
}
else {
    Replace-Atomically $BackupSettingsPath $ActiveSettingsPath
    $afterHash = Assert-Hash $ActiveSettingsPath $ExpectedPreviousSha256 'Restored settings'
}

$record = [ordered]@{
    schema = 'veriswarm.factorycity.settings_switch.v1'
    status = 'COMPLETE'
    action = $Action
    prepared_at_utc = $preparedRecord.prepared_at_utc
    completed_at_utc = [DateTime]::UtcNow.ToString('o')
    active_settings_path = $ActiveSettingsPath
    candidate_settings_path = $CandidateSettingsPath
    backup_settings_path = $BackupSettingsPath
    before_sha256 = $beforeHash
    after_sha256 = $afterHash
    candidate_sha256 = $candidateHash
    previous_sha256 = $ExpectedPreviousSha256.ToUpperInvariant()
}
Write-RecordAtomically $OperationRecordPath $record
$record
