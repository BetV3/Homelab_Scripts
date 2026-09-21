#!/usr/bin/env python3
"""Provision obs-01: the observability host.

Reuses the recipe proven on staging/prod (facts/kubernetes-envs.md):
  * BIOS, not EFI -- the Ubuntu cloud VMDK has no EFI system partition
  * vmxnet3, not the E1000 default (E1000 enumerates as ens160)
  * metadata as YAML with match:{name: ens*} + set-name, never a hardcoded key
  * datastore.mkdir first; datastore.cp will not create the target dir
  * -disk <path> -link=false; `-disk 0` segfaults govc

Placement: esxi08. NOT esxi02 (concentration risk), NOT esxi05 (the N+1
sacrifice host -- putting the observer there means losing it in the exact
scenario you need it), NOT a k8s node (an outage would take the dashboard
showing the outage -- same logic as "a watchdog must run outside Hermes").
"""
import base64
import gzip
import json
import os
import subprocess

_env = {}
for _l in open("~/.hermes/.vcenter_admin"):
    _l = _l.strip()
    if "=" in _l and not _l.startswith("#"):
        _k, _v = _l.split("=", 1)
        _env[_k] = _v
os.environ.update(
    GOVC_URL=f"https://{_env['VCENTER_HOST']}",
    GOVC_USERNAME=_env["VCENTER_ADMIN_USER"],
    GOVC_PASSWORD=_env["VCENTER_ADMIN_PASS"],
    GOVC_INSECURE="1",
)
G = "~/.local/bin/govc"
DS, NET = "VMPool", "PG-K8S-MGMT-NODES"
SRC = "iso/ubuntu-24.04-cloudimg.vmdk"
FOLDER = "Talos"
GW, PREFIX = "10.110.0.1", 24

NAME, IP, CPU, MEM, HOST, DISK_GB = "obs-01", "${OBS_HOST}", 4, 8192, "esxi08", 120

PUBKEY = open("~/.ssh/elvis.pub").read().strip()
WDKEY = subprocess.run(
    ["ssh", "-i", "~/.ssh/elvis", "-o", "StrictHostKeyChecking=no",
     "bet@${WATCHDOG_HOST}", "cat ~/.ssh/watchdog.pub"],
    capture_output=True, text=True).stdout.strip()
CA = open("~/homelab-ca.crt").read().rstrip() if os.path.exists(
    "~/homelab-ca.crt") else ""


def run(args, check=True):
    p = subprocess.run(args, capture_output=True, text=True)
    if check and p.returncode != 0:
        print("   ERR:", (p.stderr or p.stdout).strip()[:240])
    return p.returncode, (p.stdout + p.stderr).strip()


def b64gz(s):
    return base64.b64encode(gzip.compress(s.encode())).decode()


metadata = f"""instance-id: {NAME}
local-hostname: {NAME}
network:
  version: 2
  ethernets:
    id0:
      match:
        name: ens*
      set-name: ens192
      addresses:
        - {IP}/{PREFIX}
      routes:
        - to: default
          via: {GW}
      nameservers:
        addresses: [${DNS_VIP}, 1.1.1.1]
"""

ca_block = ""
if CA:
    ind = "\n".join("      " + ln for ln in CA.splitlines())
    ca_block = ("  - path: /usr/local/share/ca-certificates/homelab-ca.crt\n"
                "    permissions: '0644'\n    content: |\n" + ind + "\n")

# The watchdog key goes in at build time. On the k8s nodes it was omitted and
# every signal failed closed with "Permission denied" on a healthy cluster.
keys = f"      - {PUBKEY}\n"
if WDKEY:
    keys += f"      - {WDKEY}\n"

userdata = f"""#cloud-config
hostname: {NAME}
fqdn: {NAME}.home.example.com
preserve_hostname: false
users:
  - name: bet
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    lock_passwd: true
    ssh_authorized_keys:
{keys}write_files:
  - path: /etc/systemd/resolved.conf.d/10-homelab.conf
    permissions: '0644'
    content: |
      [Resolve]
      DNS=${DNS_VIP}
      Domains=~home.example.com
      FallbackDNS=1.1.1.1
{ca_block}runcmd:
  - systemctl restart systemd-resolved
  - update-ca-certificates
  - growpart /dev/sda 1 || true
  - resize2fs /dev/sda1 || true
package_update: true
packages: [curl, open-vm-tools, cloud-guest-utils, ca-certificates, gnupg]
"""

print(f"=== {NAME}  {IP}  {CPU}c/{MEM}MB  {DISK_GB}GB on {HOST}")
rc, out = run([G, "vm.info", NAME], check=False)
if "Name:" in out:
    print("   already exists -- not touching it")
    raise SystemExit(0)

run([G, "datastore.mkdir", "-ds", DS, "-p", NAME], check=False)
rc, _ = run([G, "datastore.cp", "-ds", DS, SRC, f"{NAME}/{NAME}.vmdk"])
if rc != 0:
    raise SystemExit("disk copy failed")

rc, _ = run([G, "vm.create", "-on=false", "-c", str(CPU), "-m", str(MEM),
             "-net", NET, "-net.adapter", "vmxnet3",
             "-ds", DS, "-host", f"{HOST}-vcenter.example.internal",
             "-folder", f"/Homelab/vm/{FOLDER}",
             "-g", "ubuntu64Guest", "-firmware", "bios",
             "-disk.controller", "pvscsi",
             "-disk", f"{NAME}/{NAME}.vmdk", "-link=false", NAME])
if rc != 0:
    raise SystemExit("create failed")

run([G, "vm.disk.change", "-vm", NAME, "-disk.label", "Hard disk 1",
     "-size", f"{DISK_GB}G"], check=False)
run([G, "vm.change", "-vm", NAME,
     "-e", f"guestinfo.metadata={b64gz(metadata)}",
     "-e", "guestinfo.metadata.encoding=gzip+base64",
     "-e", f"guestinfo.userdata={b64gz(userdata)}",
     "-e", "guestinfo.userdata.encoding=gzip+base64"])
run([G, "vm.power", "-on", NAME])
print("   created + powered on")
