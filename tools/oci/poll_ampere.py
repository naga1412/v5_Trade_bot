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

# Subnet OCID — public subnet of trading-radar-vcn in ap-hyderabad-1.
SUBNET_OCID = "ocid1.subnet.oc1.ap-hyderabad-1.aaaaaaaalbnpda2pugcwqfht6kbfijhfamtxlid7hc6aky46t3hkh3xdkvfq"

# Image OCID — leave as None and the script will auto-pick the latest
# Ubuntu 22.04 ARM64 image. Or set explicitly to pin a specific image.
IMAGE_OCID: str | None = None

# Compartment OCID — root tenancy compartment for nagarajan1998yuva.
COMPARTMENT_OCID = "ocid1.tenancy.oc1..aaaaaaaarsxww3q57tsclevjxkhdb5asnsw66en7tau5oc3ocismyewqvdia"

# Display name for the instance.
INSTANCE_NAME = "trading-radar"

# SSH public key — content of ~/.ssh/oracle_key.pub
SSH_PUBLIC_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJZj9FaBkmaLHHM8Jf0DWrW3oYkwaP3//LMjldBy5jd0 oracle-trading-radar"
)

# Shape config (Always Free max).
SHAPE = "VM.Standard.A1.Flex"
BOOT_VOLUME_GB = 100

# Poll interval seconds.
POLL_INTERVAL = 60

# Capacity-fallback ladder. After SHAPE_FALLBACK_AFTER_ATTEMPTS failed attempts
# at the primary (4 OCPU / 24 GB) shape, drop to the next entry which has a
# bigger capacity pool per AD. Always Free allows up to 4 OCPU + 24 GB total
# across instances, so a 2 OCPU instance leaves room to add a second 2 OCPU
# later (or resize the first one in-place — Oracle supports flex resize without
# reboot for A1).
SHAPE_FALLBACK_AFTER_ATTEMPTS = 50  # ~50 minutes at POLL_INTERVAL=60
SHAPE_FALLBACK_LADDER: list[tuple[int, int]] = [
    (4, 24),  # primary — full Always Free max
    (2, 12),  # half — leaves room for a second 2 OCPU instance later
    (1, 6),   # minimum useful — DB only, less than ideal
]

# Multi-region rotation. Add (region, subnet_ocid) tuples for additional
# regions to triple capacity hit rate (Mumbai + Singapore + Hyderabad have
# independent pools). To add: subscribe tenancy to region in Oracle Console ->
# Account Mgmt -> Region Management, create a VCN + public subnet via the
# Network Wizard, then paste here. Compartment OCID is tenancy-global — same.
EXTRA_REGION_SUBNETS: list[tuple[str, str]] = [
    # ("ap-mumbai-1",    "ocid1.subnet.oc1.ap-mumbai-1.aaaaaaaa..."),
    # ("ap-singapore-1", "ocid1.subnet.oc1.ap-singapore-1.aaaaaaaa..."),
]

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


def try_launch(
    client: oci.core.ComputeClient,
    *,
    ad: str,
    image_id: str,
    subnet_ocid: str,
    ocpus: int,
    memory_gb: int,
) -> str | None:
    details = oci.core.models.LaunchInstanceDetails(
        availability_domain=ad,
        compartment_id=COMPARTMENT_OCID,
        shape=SHAPE,
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=ocpus,
            memory_in_gbs=memory_gb,
        ),
        display_name=INSTANCE_NAME,
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=subnet_ocid,
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
        msg = (e.message or "").lower()
        if e.status in (500, 503) and "out of capacity" in msg:
            return None
        if e.status == 500 and "out of host capacity" in msg:
            return None
        if e.status == 429:  # too many requests
            log("  rate-limited; sleeping 600s")
            time.sleep(600)
            return None
        # Real Oracle service error — surface and stop
        log(f"  ERROR ({e.status}): {e.message}")
        raise
    except oci.exceptions.RequestException as e:
        # Transient network error (TCP drop, TLS reset, DNS blip, ISP hiccup).
        # Common; safe to swallow and retry on the next loop iteration.
        msg = str(e).split(":", 1)[-1].strip()[:120]
        log(f"  network error (will retry): {msg}")
        return None
    except (ConnectionError, TimeoutError, OSError) as e:
        # Lower-level network errors (e.g. socket reset) that bypass oci's wrapper.
        log(f"  network error (will retry): {type(e).__name__}: {str(e)[:120]}")
        return None


def _build_region_context(base_config: dict, region: str, subnet_ocid: str) -> dict:
    """Build per-region clients + cached AD list + image OCID."""
    region_config = {**base_config, "region": region}
    compute = oci.core.ComputeClient(region_config)
    identity = oci.identity.IdentityClient(region_config)
    ads = list_ads(identity, COMPARTMENT_OCID)
    log(f"  region {region}: ADs = {ads}")
    image_id = IMAGE_OCID or find_ubuntu_arm_image(compute, COMPARTMENT_OCID)
    return {
        "region": region,
        "config": region_config,
        "compute": compute,
        "ads": ads,
        "image_id": image_id,
        "subnet_ocid": subnet_ocid,
    }


def _report_success(ctx: dict, instance_id: str) -> None:
    """Wait for RUNNING, then look up and print the public IP + SSH command."""
    log("=" * 60)
    log("SUCCESS! Instance provisioning started.")
    log(f"Region:        {ctx['region']}")
    log(f"Instance OCID: {instance_id}")
    log("Waiting for it to reach RUNNING state...")
    compute: oci.core.ComputeClient = ctx["compute"]
    try:
        oci.wait_until(
            compute,
            compute.get_instance(instance_id),
            "lifecycle_state",
            "RUNNING",
            max_wait_seconds=600,
        )
    except Exception:  # noqa: BLE001
        log("Could not wait for RUNNING; check OCI console manually.")
        log(f"Instance OCID: {instance_id}")
        return

    vnic_attachments = compute.list_vnic_attachments(
        compartment_id=COMPARTMENT_OCID,
        instance_id=instance_id,
    ).data
    if vnic_attachments:
        network = oci.core.VirtualNetworkClient(ctx["config"])
        vnic = network.get_vnic(vnic_attachments[0].vnic_id).data
        log(f"PUBLIC IP: {vnic.public_ip}")
        log(f"SSH: ssh -i ~/.ssh/oracle_key ubuntu@{vnic.public_ip}")
    log("=" * 60)


def main() -> int:
    if not Path(OCI_CONFIG_FILE).exists():
        print(f"ERROR: OCI config not found at {OCI_CONFIG_FILE}", file=sys.stderr)
        print("Run `oci setup config` first.", file=sys.stderr)
        return 1

    if "PASTE_" in SUBNET_OCID or "PASTE_" in COMPARTMENT_OCID or "PASTE_" in SSH_PUBLIC_KEY:
        print("ERROR: edit poll_ampere.py and fill in SUBNET_OCID, COMPARTMENT_OCID, SSH_PUBLIC_KEY",
              file=sys.stderr)
        return 1

    base_config = oci.config.from_file(file_location=OCI_CONFIG_FILE, profile_name=OCI_PROFILE)
    base_config["region"] = REGION
    oci.config.validate_config(base_config)

    # Build a context per region. EXTRA_REGION_SUBNETS expands the search pool;
    # each region has independent capacity, so 3 regions ≈ 3× hit rate.
    region_pairs: list[tuple[str, str]] = [(REGION, SUBNET_OCID), *EXTRA_REGION_SUBNETS]
    log(f"Initializing {len(region_pairs)} region(s): {[r for r, _ in region_pairs]}")
    contexts = [_build_region_context(base_config, region, subnet) for region, subnet in region_pairs]

    log(f"Polling for Ampere capacity every {POLL_INTERVAL}s.")
    log(f"Shape ladder: {SHAPE_FALLBACK_LADDER} (drops one rung after "
        f"{SHAPE_FALLBACK_AFTER_ATTEMPTS} failed attempts at the current rung).")
    log("Press Ctrl+C to stop.")

    attempt = 0
    shape_idx = 0
    attempts_at_current_shape = 0

    while True:
        attempt += 1
        attempts_at_current_shape += 1
        ocpus, memory_gb = SHAPE_FALLBACK_LADDER[shape_idx]

        for ctx in contexts:
            for ad in ctx["ads"]:
                try:
                    instance_id = try_launch(
                        ctx["compute"],
                        ad=ad,
                        image_id=ctx["image_id"],
                        subnet_ocid=ctx["subnet_ocid"],
                        ocpus=ocpus,
                        memory_gb=memory_gb,
                    )
                except Exception:  # noqa: BLE001
                    log(f"  attempt {attempt} region={ctx['region']} ad={ad}: hard error\n"
                        f"{traceback.format_exc()}")
                    time.sleep(POLL_INTERVAL)
                    continue

                if instance_id:
                    _report_success(ctx, instance_id)
                    return 0

                log(f"  attempt {attempt} region={ctx['region']} ad={ad} "
                    f"shape={ocpus}/{memory_gb}: out of capacity")

        # Promote to the next shape rung if we've been stuck long enough
        # at the current one and a smaller fallback is available.
        if (
            attempts_at_current_shape >= SHAPE_FALLBACK_AFTER_ATTEMPTS
            and shape_idx < len(SHAPE_FALLBACK_LADDER) - 1
        ):
            shape_idx += 1
            attempts_at_current_shape = 0
            new_ocpus, new_mem = SHAPE_FALLBACK_LADDER[shape_idx]
            log("=" * 60)
            log(f"FALLBACK: dropping to {new_ocpus} OCPU / {new_mem} GB after "
                f"{SHAPE_FALLBACK_AFTER_ATTEMPTS} failed attempts at "
                f"{ocpus} OCPU / {memory_gb} GB.")
            log(f"You can flex-resize back up to {SHAPE_FALLBACK_LADDER[0]} once "
                f"capacity returns (no reboot needed for A1).")
            log("=" * 60)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("Interrupted by user.")
        sys.exit(130)
