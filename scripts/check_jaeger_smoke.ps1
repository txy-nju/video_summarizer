param(
    [string]$JaegerBaseUrl = "http://localhost:16686",
    [string]$ServiceName = "video-summarizer-backend",
    [string]$Lookback = "1h",
    [int]$Limit = 20,
    [string[]]$RequiredOperations = @(
        "http.request.handle",
        "celery.task.start",
        "llm.inference.generate"
    )
)

$ErrorActionPreference = "Stop"

function New-QueryString {
    param(
        [hashtable]$Query
    )

    if (-not $Query -or $Query.Count -eq 0) {
        return ""
    }

    $pairs = foreach ($entry in $Query.GetEnumerator()) {
        $key = [System.Uri]::EscapeDataString([string]$entry.Key)
        $value = [System.Uri]::EscapeDataString([string]$entry.Value)
        "{0}={1}" -f $key, $value
    }

    return "?" + ($pairs -join "&")
}

function Invoke-JaegerApi {
    param(
        [string]$Path,
        [hashtable]$Query = @{}
    )

    $normalizedBaseUrl = $JaegerBaseUrl.TrimEnd("/")
    $uri = "{0}{1}{2}" -f $normalizedBaseUrl, $Path, (New-QueryString -Query $Query)
    return Invoke-RestMethod -Method Get -Uri $uri -TimeoutSec 10
}

function Get-TraceOperations {
    param(
        [object[]]$Traces
    )

    $operations = New-Object System.Collections.Generic.HashSet[string] ([System.StringComparer]::OrdinalIgnoreCase)

    foreach ($trace in $Traces) {
        if ($null -eq $trace) {
            continue
        }

        if ($trace.PSObject.Properties.Name -contains "spans") {
            foreach ($span in @($trace.spans)) {
                if ($span -and $span.PSObject.Properties.Name -contains "operationName" -and $span.operationName) {
                    [void]$operations.Add([string]$span.operationName)
                }
            }
            continue
        }

        foreach ($span in @($trace)) {
            if ($span -and $span.PSObject.Properties.Name -contains "operationName" -and $span.operationName) {
                [void]$operations.Add([string]$span.operationName)
            }
        }
    }

    return $operations
}

try {
    Write-Host "Checking Jaeger health at $JaegerBaseUrl ..."
    $servicesResponse = Invoke-JaegerApi -Path "/api/services"
    $services = @($servicesResponse.data)

    if (-not $services -or $services.Count -eq 0) {
        throw "Jaeger is reachable, but no services are indexed yet. Trigger one traced request first."
    }

    Write-Host ("Indexed services: {0}" -f ($services -join ", "))

    if ($ServiceName -notin $services) {
        throw ("Service '{0}' not found in Jaeger. Check OTEL_SERVICE_NAME and ensure backend/worker have emitted spans." -f $ServiceName)
    }

    Write-Host ("Service '{0}' is visible." -f $ServiceName)

    $tracesResponse = Invoke-JaegerApi -Path "/api/traces" -Query @{
        service = $ServiceName
        lookback = $Lookback
        limit = $Limit
    }

    $traces = @($tracesResponse.data)
    if (-not $traces -or $traces.Count -eq 0) {
        throw ("Service '{0}' is visible, but no traces were found in the last {1}. Trigger one request and retry." -f $ServiceName, $Lookback)
    }

    Write-Host ("Found {0} trace(s) for service '{1}' in the last {2}." -f $traces.Count, $ServiceName, $Lookback)

    $operations = Get-TraceOperations -Traces $traces
    $operationList = @($operations)
    if ($operationList.Count -gt 0) {
        $observedOperations = ($operationList | Sort-Object | Select-Object -Unique -First 20)
        Write-Host ("Observed operations: {0}" -f ($observedOperations -join ", "))
    }

    $missingOperations = @()
    foreach ($requiredOperation in $RequiredOperations) {
        if (-not $operations.Contains($requiredOperation)) {
            $missingOperations += $requiredOperation
        }
    }

    if ($missingOperations.Count -gt 0) {
        throw ("Missing required operations: {0}. Trigger a fuller workflow path or widen -Lookback." -f ($missingOperations -join ", "))
    }

    Write-Host "Jaeger smoke check passed."
    exit 0
}
catch {
    $message = $_.Exception.Message
    if ($message -match "无法连接到远程服务器|Unable to connect to the remote server") {
        $message = "Jaeger is not reachable. Start it with ./scripts/start_jaeger_local.ps1 and retry."
    }

    Write-Host ("Jaeger smoke check failed: {0}" -f $message) -ForegroundColor Red
    exit 1
}