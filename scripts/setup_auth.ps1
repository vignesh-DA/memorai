# Install Authentication Dependencies
Write-Host "📦 Installing authentication packages..." -ForegroundColor Cyan

# Install new dependencies
pip install python-jose[cryptography]==3.3.0
pip install "passlib[bcrypt]==1.7.4"

Write-Host "✅ Authentication packages installed" -ForegroundColor Green

# Generate secure JWT secret key
Write-Host "`n🔐 Generating secure JWT secret key..." -ForegroundColor Cyan

$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$secret_key = [System.BitConverter]::ToString($bytes) -replace '-', ''

Write-Host "✅ JWT Secret Key generated" -ForegroundColor Green
Write-Host "`nAdd this to your .env file:" -ForegroundColor Yellow
Write-Host "JWT_SECRET_KEY=$secret_key" -ForegroundColor White

# Append to .env if it exists
if (Test-Path ".env") {
    if (-not (Select-String -Path ".env" -Pattern "JWT_SECRET_KEY" -Quiet)) {
        Write-Host "`n📝 Adding JWT_SECRET_KEY to .env file..." -ForegroundColor Cyan
        Add-Content -Path ".env" -Value "`n# Authentication`nJWT_SECRET_KEY=$secret_key"
        Write-Host "✅ JWT_SECRET_KEY added to .env" -ForegroundColor Green
    } else {
        Write-Host "`n⚠️  JWT_SECRET_KEY already exists in .env" -ForegroundColor Yellow
    }
} else {
    Write-Host "`n⚠️  .env file not found. Create one from .env.example" -ForegroundColor Yellow
}

Write-Host "`n✨ Authentication setup complete!" -ForegroundColor Green
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "1. Restart the server: uvicorn app.main:app --reload" -ForegroundColor White
Write-Host "2. Register a user: POST /api/v1/auth/register" -ForegroundColor White
Write-Host "3. Login: POST /api/v1/auth/login" -ForegroundColor White
