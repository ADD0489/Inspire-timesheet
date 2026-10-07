# Inspire Kitchens: let the timesheet Worker send email ONLY as timesheets@inspirekitchens.co.nz.
# Run once by a Microsoft 365 admin (Aaron), in PowerShell with the ExchangeOnlineManagement module.
# Uses Exchange "RBAC for Applications" (Microsoft's replacement for Application Access Policies).
# IMPORTANT: do NOT grant the Mail.Send application permission in Entra ID for this app. That grant
# would let it send as ANY mailbox and would bypass this one-mailbox scope.
#
# Before running:
#   1. Exchange admin centre > Recipients > Mailboxes > Add a shared mailbox:
#        Display name "Inspire Kitchens Timesheets", email timesheets@inspirekitchens.co.nz (free, no licence)
#   2. Entra admin centre > App registrations > New registration:
#        Name "Inspire Timesheets Mailer", single tenant, no redirect URI.
#        Copy the Application (client) ID and Directory (tenant) ID.
#        Certificates & secrets > New client secret (24 months). Copy the secret VALUE.
#        Enterprise applications > Inspire Timesheets Mailer > copy its Object ID (used below).

$AppId    = "<Application (client) ID>"
$ObjectId = "<Enterprise application Object ID>"
$Mailbox  = "timesheets@inspirekitchens.co.nz"

Install-Module ExchangeOnlineManagement -Scope CurrentUser   # skip if already installed
Connect-ExchangeOnline

New-ServicePrincipal -AppId $AppId -ObjectId $ObjectId -DisplayName "Inspire Timesheets Mailer"
New-ManagementScope -Name "Timesheets mailbox only" -RecipientRestrictionFilter "PrimarySmtpAddress -eq '$Mailbox'"
New-ManagementRoleAssignment -App $AppId -Role "Application Mail.Send" -CustomResourceScope "Timesheets mailbox only"

# Check: the first should say InScope True, the second (any other mailbox) InScope False.
Test-ServicePrincipalAuthorization -Identity $AppId -Resource $Mailbox
Test-ServicePrincipalAuthorization -Identity $AppId -Resource "aaron@inspirekitchens.co.nz"

# Optional: hide the shared mailbox from the address book
Set-Mailbox $Mailbox -HiddenFromAddressListsEnabled $true
