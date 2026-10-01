# Admin Guide — Troubleshooting Blocked USB Devices

## A USB device is blocked unexpectedly
If a user reports that an approved USB device is blocked, work through these steps:
1. Open the Device Control console and search the device serial number under Devices > Inventory.
2. Confirm the device is assigned to an allow policy for the user's group; group membership syncs every 15 minutes.
3. Check whether the endpoint agent version is 4.2 or later; older agents do not support per-serial allow rules.
4. Run "dcctl policy refresh" on the endpoint to force an immediate policy update.
5. If the device still fails, export the agent log with "dcctl logs --since 1h" and attach it to the support case.

## Printers and docking stations
Printers and docking stations are classified as non-storage peripherals and are allowed by default. If they are blocked, check that the "Block all unknown classes" setting is disabled for the affected policy.
