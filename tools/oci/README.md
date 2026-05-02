# Oracle Ampere A1 Polling — `tools/oci/poll_ampere.py`

A pure-Python script that retries `compute.launch_instance` every 60 seconds
until Oracle's Always-Free Ampere capacity opens. Replaces the PHP-based
`hitrov/oci-arm-host-capacity` repo with a simpler dependency profile.

## Prerequisites

- Python 3.11 with `oci` SDK installed (already done if you ran
  `py -3.11 -m pip install --user oci-cli`)
- OCI CLI configured at `~/.oci/config` (run `oci setup config`)
- Public API key uploaded to OCI console under your user
- A VCN + public subnet exists in your tenancy
- SSH keypair generated at `~/.ssh/oracle_key`

## One-time setup

### 1. Run `oci setup config`

In PowerShell:

```powershell
oci setup config
```

It interactively asks for:
- Location of config file: just press Enter (default `C:\Users\nagar\.oci\config`)
- User OCID: paste from OCI console (Profile icon → User Settings → OCID)
- Tenancy OCID: paste from OCI console (Profile icon → Tenancy: name → OCID)
- Region: `ap-hyderabad-1`
- Generate a new RSA keypair: **Y**
- Directory for the keys: just press Enter (default `C:\Users\nagar\.oci\`)
- Name for keys: just press Enter (default `oci_api_key`)
- Passphrase: leave empty (just press Enter)

After it runs, it prints the **fingerprint** of the new key. Note it.

### 2. Upload the API public key to OCI

In OCI console (https://cloud.oracle.com):

1. Top-right profile icon → **User Settings**
2. Left sidebar → **API Keys**
3. **Add API Key** → choose **Paste Public Key** (or upload file from
   `C:\Users\nagar\.oci\oci_api_key_public.pem`)
4. Paste contents → **Add**
5. The fingerprint shown should match what `oci setup config` printed.

### 3. Create a VCN + public subnet (one-time)

In OCI console:

1. Top-left hamburger menu → **Networking** → **Virtual Cloud Networks**
2. **Start VCN Wizard** → "Create VCN with Internet Connectivity" → **Start
   VCN Wizard**
3. VCN Name: `trading-radar-vcn`
4. Compartment: leave default (your root tenancy compartment)
5. Defaults are fine for everything else → **Next** → **Create**
6. Wait ~30 seconds. When done, click into the new VCN.
7. **Subnets** tab → click the **public subnet** (named like
   `Public Subnet-trading-radar-vcn`)
8. Copy its **OCID** — you'll paste this into `poll_ampere.py`

### 4. Edit `poll_ampere.py` config block

Open `a:\v5_Trade_bot\tools\oci\poll_ampere.py` and fill in:

- `SUBNET_OCID` — paste the public-subnet OCID from step 3.8
- `COMPARTMENT_OCID` — paste your **tenancy OCID** (same one you put in
  `oci setup config`)
- `SSH_PUBLIC_KEY` — paste the contents of `C:\Users\nagar\.ssh\oracle_key.pub`
  (one line, surrounded by quotes)

Other defaults are fine (region `ap-hyderabad-1`, shape A1.Flex, 4 OCPU, 24 GB).

## Running

In PowerShell:

```powershell
cd A:\v5_Trade_bot
py -3.11 tools\oci\poll_ampere.py
```

It will:

1. Validate your OCI config
2. List availability domains in your region
3. Auto-pick the latest Ubuntu 22.04 ARM64 image
4. Loop forever, trying to launch in each AD, sleeping 60s between rounds

Leave it running. Could be 1 hour, could be 14 days. When capacity opens,
the script:
- Launches the instance
- Waits for it to reach RUNNING
- Prints the **public IP** + an **ssh command** to use

Then you can stop the script (it exits automatically on success).

## Stopping

Press **Ctrl+C** at any time. Safe to stop and restart.

## Common errors

| Error | Cause | Fix |
|---|---|---|
| `OCI config not found` | `oci setup config` not run | Run it |
| `validate_config failed` | Wrong fingerprint or missing key file | Re-run `oci setup config`, re-upload public key |
| `ServiceError 404 Compartment not found` | Wrong COMPARTMENT_OCID | Use your tenancy OCID, not user OCID |
| `ServiceError 404 Subnet not found` | Wrong SUBNET_OCID or wrong region | Confirm subnet is in `ap-hyderabad-1` |
| `Out of host capacity` (every attempt) | Oracle is full in your region | Keep polling; this is expected |

## After success

Once the script prints the public IP, follow Phase 7 of
`docs/superpowers/SHIPPING_RUNBOOK.md` to deploy the trading-radar stack
to the VM.
