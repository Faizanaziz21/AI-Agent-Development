# Microsoft Intune Integration Guide

## Setup
Register the Device Control app in Entra ID, grant DeviceManagementManagedDevices.Read.All, and paste the tenant ID into Settings > Integrations > Intune.

## Troubleshooting sync failures
1. Verify the client secret has not expired in Entra ID.
2. Check that the service principal has the required Graph permission and admin consent was granted.
3. Sync runs every 30 minutes; trigger a manual sync from Settings > Integrations > Intune > Sync now.
4. Devices missing from the sync usually lack a compliance policy assignment in Intune.
