[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SourceProjectRoot,

    [Parameter(Mandatory = $true)]
    [string]$AirSimPluginRoot,

    [Parameter(Mandatory = $true)]
    [string]$DestinationProjectRoot,

    [Parameter(Mandatory = $true)]
    [string]$SourceProjectFileName,

    [Parameter(Mandatory = $true)]
    [string]$WorldRelativePath,

    [Parameter(Mandatory = $true)]
    [string]$WorldObjectPath,

    [Parameter(Mandatory = $true)]
    [string]$GameModeClass,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Fa-f]{64}$')]
    [string]$ExpectedSourceProjectSha256,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Fa-f]{64}$')]
    [string]$ExpectedWorldSha256,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Fa-f]{64}$')]
    [string]$ExpectedPluginDescriptorSha256,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Fa-f]{64}$')]
    [string]$ExpectedEditorPluginDllSha256
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Resolve-ExistingDirectory {
    param([string]$Path, [string]$Label)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "$Label directory does not exist: $Path"
    }
    return (Resolve-Path -LiteralPath $Path).Path
}

function Assert-FileHash {
    param([string]$Path, [string]$Expected, [string]$Label)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label file does not exist: $Path"
    }
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
    if ($actual -ne $Expected.ToUpperInvariant()) {
        throw "$Label hash mismatch: expected $Expected, got $actual"
    }
    return $actual
}

function Copy-RequiredDirectory {
    param([string]$SourceParent, [string]$Name, [string]$DestinationParent)
    $source = Join-Path $SourceParent $Name
    if (-not (Test-Path -LiteralPath $source -PathType Container)) {
        throw "Required directory does not exist: $source"
    }
    Copy-Item -LiteralPath $source -Destination $DestinationParent -Recurse
}

if (Get-Process -Name UnrealEditor -ErrorAction SilentlyContinue) {
    throw 'Close UnrealEditor before provisioning the derived project.'
}

$sourceRoot = Resolve-ExistingDirectory $SourceProjectRoot 'Source project'
$pluginRoot = Resolve-ExistingDirectory $AirSimPluginRoot 'AirSim plugin'
$destinationParent = Split-Path -Parent $DestinationProjectRoot
if (-not $destinationParent) {
    throw 'Destination project root must have an explicit parent directory.'
}
if (-not (Test-Path -LiteralPath $destinationParent -PathType Container)) {
    throw "Destination parent does not exist: $destinationParent"
}
$destinationParent = (Resolve-Path -LiteralPath $destinationParent).Path
$destinationLeaf = Split-Path -Leaf $DestinationProjectRoot
$destinationRoot = Join-Path $destinationParent $destinationLeaf
if (Test-Path -LiteralPath $destinationRoot) {
    throw "Destination already exists and will not be overwritten: $destinationRoot"
}
if ($destinationRoot.StartsWith($sourceRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Destination must not be inside the protected source project.'
}

$sourceProject = Join-Path $sourceRoot $SourceProjectFileName
$sourceWorld = Join-Path $sourceRoot $WorldRelativePath
$pluginDescriptor = Get-ChildItem -LiteralPath $pluginRoot -Filter '*.uplugin' -File
if (@($pluginDescriptor).Count -ne 1) {
    throw 'AirSim plugin root must contain exactly one .uplugin descriptor.'
}
$pluginDescriptor = $pluginDescriptor[0].FullName
$pluginName = [IO.Path]::GetFileNameWithoutExtension($pluginDescriptor)
$editorDll = Join-Path $pluginRoot "Binaries\Win64\UnrealEditor-$pluginName.dll"

$sourceProjectHash = Assert-FileHash $sourceProject $ExpectedSourceProjectSha256 'Source project'
$sourceWorldHash = Assert-FileHash $sourceWorld $ExpectedWorldSha256 'Source world'
$pluginDescriptorHash = Assert-FileHash $pluginDescriptor $ExpectedPluginDescriptorSha256 'Plugin descriptor'
$editorDllHash = Assert-FileHash $editorDll $ExpectedEditorPluginDllSha256 'Editor plugin DLL'

New-Item -ItemType Directory -Path $destinationRoot | Out-Null
Copy-RequiredDirectory $sourceRoot 'Config' $destinationRoot
Copy-RequiredDirectory $sourceRoot 'Content' $destinationRoot
Copy-Item -LiteralPath $sourceProject -Destination $destinationRoot

$destinationPlugins = New-Item -ItemType Directory -Path (Join-Path $destinationRoot 'Plugins')
$destinationPlugin = New-Item -ItemType Directory -Path (Join-Path $destinationPlugins.FullName $pluginName)
foreach ($directory in @('Binaries', 'Content', 'Source')) {
    Copy-RequiredDirectory $pluginRoot $directory $destinationPlugin.FullName
}
Copy-Item -LiteralPath $pluginDescriptor -Destination $destinationPlugin.FullName

$destinationProject = Join-Path $destinationRoot $SourceProjectFileName
$projectJson = Get-Content -LiteralPath $destinationProject -Raw | ConvertFrom-Json
$pluginEntries = [System.Collections.Generic.List[object]]::new()
if ($projectJson.PSObject.Properties.Name -contains 'Plugins') {
    foreach ($entry in $projectJson.Plugins) {
        if ($entry.Name -ne $pluginName) {
            $pluginEntries.Add($entry)
        }
    }
}
$pluginEntries.Add([ordered]@{ Name = $pluginName; Enabled = $true })
$pluginMetadata = Get-Content -LiteralPath $pluginDescriptor -Raw | ConvertFrom-Json
if ($pluginMetadata.PSObject.Properties.Name -contains 'Plugins') {
    foreach ($dependency in $pluginMetadata.Plugins) {
        if (-not ($pluginEntries | Where-Object { $_.Name -eq $dependency.Name })) {
            $pluginEntries.Add([ordered]@{
                Name = $dependency.Name
                Enabled = [bool]$dependency.Enabled
            })
        }
    }
}
$projectJson.Plugins = @($pluginEntries)
$projectJson.Description = 'Derived FactoryCity CoSys integration project; source remains protected'
$projectText = ($projectJson | ConvertTo-Json -Depth 20) + "`n"
[IO.File]::WriteAllText($destinationProject, $projectText, [Text.UTF8Encoding]::new($false))

$engineConfig = Join-Path $destinationRoot 'Config\DefaultEngine.ini'
$override = @"

; Generated Phase 3 overrides. Values are supplied by the validated invocation.
[/Script/EngineSettings.GameMapsSettings]
EditorStartupMap=$WorldObjectPath
GameDefaultMap=$WorldObjectPath
GlobalDefaultGameMode=$GameModeClass
"@
[IO.File]::AppendAllText($engineConfig, $override, [Text.UTF8Encoding]::new($false))

$forbidden = @(
    Get-ChildItem -LiteralPath $destinationRoot -Directory -Recurse |
        Where-Object { $_.Name -in @('DerivedDataCache', 'Intermediate', 'Saved', '.vs') }
)
if ($forbidden.Count -ne 0) {
    throw "Derived project contains forbidden transient directories: $($forbidden.FullName -join ', ')"
}

$projectDescriptor = Get-Content -LiteralPath $destinationProject -Raw | ConvertFrom-Json
$enabledPluginNames = @(
    $projectDescriptor.Plugins |
        Where-Object { $_.Enabled } |
        ForEach-Object { $_.Name }
)
if ($enabledPluginNames -notcontains $pluginName) {
    throw 'Derived project does not enable the supplied AirSim plugin.'
}
if (-not (Test-Path -LiteralPath (Join-Path $destinationRoot $WorldRelativePath))) {
    throw 'Derived project does not contain the requested world.'
}

$provenance = [ordered]@{
    schema = 'veriswarm.factorycity.local_derived_project.v1'
    created_at_utc = [DateTime]::UtcNow.ToString('o')
    source_project_root = $sourceRoot
    airsim_plugin_root = $pluginRoot
    destination_project_root = $destinationRoot
    source_project_sha256 = $sourceProjectHash
    source_world_sha256 = $sourceWorldHash
    plugin_descriptor_sha256 = $pluginDescriptorHash
    editor_plugin_dll_sha256 = $editorDllHash
    world_object_path = $WorldObjectPath
    game_mode_class = $GameModeClass
    enabled_plugins = $enabledPluginNames
    source_content_files = @(Get-ChildItem -LiteralPath (Join-Path $sourceRoot 'Content') -File -Recurse).Count
    derived_content_files = @(Get-ChildItem -LiteralPath (Join-Path $destinationRoot 'Content') -File -Recurse).Count
    derived_plugin_files = @(Get-ChildItem -LiteralPath $destinationPlugin.FullName -File -Recurse).Count
    forbidden_transient_directory_count = $forbidden.Count
}
$provenancePath = Join-Path $destinationRoot 'phase3_local_provenance.json'
$provenanceText = ($provenance | ConvertTo-Json -Depth 10) + "`n"
[IO.File]::WriteAllText($provenancePath, $provenanceText, [Text.UTF8Encoding]::new($false))
$provenance
