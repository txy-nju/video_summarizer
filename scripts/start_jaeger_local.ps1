param(
    [string]$ContainerName = "video-summarizer-jaeger",
    [string]$Image = "jaegertracing/all-in-one:1.57"
)

$exists = docker ps -a --format "{{.Names}}" | Where-Object { $_ -eq $ContainerName }
if ($exists) {
    Write-Host "Removing existing container: $ContainerName"
    docker rm -f $ContainerName | Out-Null
}

Write-Host "Starting Jaeger container: $ContainerName"
docker run --name $ContainerName --rm -d `
    -e COLLECTOR_OTLP_ENABLED=true `
    -p 16686:16686 `
    -p 4317:4317 `
    -p 4318:4318 `
    $Image | Out-Null

Write-Host "Jaeger UI: http://localhost:16686"
Write-Host "OTLP gRPC endpoint: http://localhost:4317"
Write-Host "OTLP HTTP endpoint: http://localhost:4318"
