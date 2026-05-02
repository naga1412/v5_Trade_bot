"""Oracle Ampere A1 capacity polling script (pure Python).

Tries to launch a 4 OCPU / 24 GB ARM Ampere instance every 60 seconds,
rotating through availability domains. Exits when one succeeds.

Setup:
  1. Run `oci setup config` once to create ~/.oci/config (interactive).
  2. Upload the public API key to OCI console -> User Settings -> API Keys.
  3. Use Oracle web UI's "Set up a network with a wizard" to create a default
     VCN + public subnet (one-time, takes 30 seconds).
  4. Edit poll_ampere_config.py with your subnet OCID, image OCID, SSH pubkey.
  5. Run: py -3.11 tools/oci/poll_ampere.py

Stop with Ctrl+C. The script handles "out of capacity" gracefully and retries.
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

try:
    import oci  # type: ignore
except ImportError:
    print("ERROR: oci SDK not found. Run: py -3.11 -m pip install --user oci", file=sys.stderr)
    sys.exit(1)


# ====================================================================
# Configuration — fill these in from your Oracle account.
# ====================================================================

# Path to the OCI config created by `oci setup config` (default location)
OCI_CONFIG_FILE = str(Path.home() / ".oci" / "config")
OCI_PROFILE = "DEFAULT"

# Region — must match your Oracle home region. Hyderabad = ap-hyderabad-1.
REGION = "ap-hyderabad-1"

# Subnet OCID — get from OCI console: Networking > Virtual Cloud Networks >
# (your VCN) > Subnets > (public subnet) > copy OCID.
SUBNET_OCID = "PASTE_SUBNET_OCID_HERE"

# Image OCID — leave as None and the script will auto-pick the latest
# Ubuntu 22.04 ARM64 image. Or set explicitly to pin a specific image.
IMAGE_OCID: str | None = None

# Compartment OCID — usually equals tenancy OCID for personal accounts.
COMPARTMENT_OCID = "PASTE_TENANCY_OCID_HERE"

# Display name for the instance.
INSTANCE_NAME = "trading-radar"

# SSH public key — content of ~/.ssh/oracle_key.pub
SSH_PUBLIC_KEY = (
    "PASTE_SSH_PUBLIC_KEY_HERE"
)

# Shape config (Always Free max).
SHAPE = "VM.Standard.A1.Flex"
OCPUS = 4
MEMORY_GB = 24
BOOT_VOLUME_GB = 100

# Poll interval seconds.
POLL_INTERVAL = 60

# ====================================================================
# Script body — no need to edit below.
# ====================================================================


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def find_ubuntu_arm_image(client: oci.core.ComputeClient, compartment: str) -> str:
    log("Searching for latest Ubuntu 22.04 ARM64 image...")
    images = oci.pagination.list_call_get_all_results(
        client.list_images,
        compartment_id=compartment,
        operating_system="Canonical Ubuntu",
        operating_system_version="22.04",
        shape=SHAPE,
        sort_by="TIMECREATED",
        sort_order="DESC",
    ).data
    arm_images = [img for img in images if "aarch64" in (img.display_name or "").lower()]
    if not arm_images:
        # Fallback: any image that lists this shape and is OS Ubuntu 22.04
        arm_images = images
    if not arm_images:
        raise RuntimeError("No Ubuntu 22.04 ARM image found in this region/compartment.")
    chosen = arm_images[0]
    log(f"Picked image: {chosen.display_name} ({chosen.id})")
    return chosen.id


def list_ads(identity_client: oci.identity.IdentityClient, compartment: str) -> list[str]:
    ads = identity_client.list_availability_domains(compartment_id=compartment).data
    return [ad.name for ad in ads]


def try_launch(client: oci.core.ComputeClient, *, ad: str, image_id: str) -> str | None:
    details = oci.core.models.LaunchInstanceDetails(
        availability_domain=ad,
        compartment_id=COMPARTMENT_OCID,
        shape=SHAPE,
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=OCPUS,
            memory_in_gbs=MEMORY_GB,
        ),
        display_name=INSTANCE_NAME,
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=SUBNET_OCID,
            assign_public_ip=True,
        ),
        source_details=oci.core.models.InstanceSourceViaImageDetails(
            image_id=image_id,
            boot_volume_size_in_gbs=BOOT_VOLUME_GB,
        ),
        metadata={"ssh_authorized_keys": SSH_PUBLIC_KEY},
    )
    try:
        resp = client.launch_instance(launch_instance_details=details)
        return resp.data.id
    except oci.exceptions.ServiceError as e:
        if e.status in (500, 503) and "out of capacity" in (e.message or "").lower():
            return None
        if e.status == 500 and "Out of host capacity" in (e.message or ""):
            return None
        if e.status == 429:  # too many requests
            log(f"  rate-limited; sleeping 600s")
            time.sleep(600)
            return None
        # Real error — surface and stop
        log(f"  ERROR ({e.status}): {e.message}")
        raise


def main() -> int:
    if not Path(OCI_CONFIG_FILE).exists():
        print(f"ERROR: OCI config not found at {OCI_CONFIG_FILE}", file=sys.stderr)
        print("Run `oci setup config` first.", file=sys.stderr)
        return 1

    if "PASTE_" in SUBNET_OCID or "PASTE_" in COMPARTMENT_OCID or "PASTE_" in SSH_PUBLIC_KEY:
        print("ERROR: edit poll_ampere.py and fill in SUBNET_OCID, COMPARTMENT_OCID, SSH_PUBLIC_KEY",
              file=sys.stderr)
        return 1

    config = oci.config.from_file(file_location=OCI_CONFIG_FILE, profile_name=OCI_PROFILE)
    config["region"] = REGION  # override in case CLI default differs
    oci.config.validate_config(config)

    compute = oci.core.ComputeClient(config)
    identity = oci.identity.IdentityClient(config)

    ads = list_ads(identity, COMPARTMENT_OCID)
    log(f"Region {REGION} availability domains: {ads}")

    image_id = IMAGE_OCID or find_ubuntu_arm_image(compute, COMPARTMENT_OCID)

    log(f"Polling for Ampere {OCPUS} OCPU / {MEMORY_GB} GB capacity, every {POLL_INTERVAL}s...")
    log("Press Ctrl+C to stop.")

    attempt = 0
    while True:
        attempt += 1
        for ad in ads:
            try:
                instance_id = try_launch(compute, ad=ad, image_id=image_id)
            except Exception:  # noqa: BLE001
                log(f"  attempt {attempt} ad={ad}: hard error\n{traceback.format_exc()}")
                time.sleep(POLL_INTERVAL)
                continue

            if instance_id:
                log("=" * 60)
                log("SUCCESS! Instance provisioning started.")
                log(f"Instance OCID: {instance_id}")
                log("Waiting for it to reach RUNNING state...")
                try:
                    instance = oci.wait_until(
                        compute,
                        compute.get_instance(instance_id),
                        "lifecycle_state",
                        "RUNNING",
                        max_wait_seconds=600,
                    ).data
                except Exception:  # noqa: BLE001
                    log(f"Could not wait for RUNNING; check OCI console manually.\n"
                        f"Instance OCID: {instance_id}")
                    return 0

                # Get the public IP
                vnic_attachments = compute.list_vnic_attachments(
                    compartment_id=COMPARTMENT_OCID,
                    instance_id=instance_id,
                ).data
                if vnic_attachments:
                    network = oci.core.VirtualNetworkClient(config)
                    vnic = network.get_vnic(vnic_attachments[0].vnic_id).data
                    log(f"PUBLIC IP: {vnic.public_ip}")
                    log(f"SSH: ssh -i ~/.ssh/oracle_key ubuntu@{vnic.public_ip}")
                log("=" * 60)
                return 0

            log(f"  attempt {attempt} ad={ad}: out of capacity")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("Interrupted by user.")
        sys.exit(130)
