# vSphere automation

Scripts that manage a 7-host ESXi cluster under vCenter 8 through the API
rather than the web client. These are the artifacts behind the figures on
the homelab project page: the numbers there were produced by running
`infra_metrics.py`, not estimated.

## Files

| File | What it does |
|---|---|
| `prov_obs01.py` | Provisions a VM end to end with `govc`: clone, CPU/RAM sizing, network, cloud-init via guestinfo, power on, wait for the guest IP. |
| `infra_metrics.py` | Reads live CPU, memory, datastore and VM counts from the vCenter API. |
| `full_inventory.py` | Whole-fleet inventory to JSON: hosts, VMs, datastores, power state. |
| `evc_check.sh` | Reports the actual CPU generation per host and which instruction sets are exposed to guests. |
| `bottleneck_probe.sh` | Measures memory bandwidth, disk throughput, and fsync latency inside a guest. |

## Two gotchas worth the reading time

**Ubuntu cloud images have no EFI system partition.** A VM created with EFI
firmware will clone, power on, and sit at a boot prompt forever with no
useful error. Every working VM in this fleet is BIOS. The firmware choice
cannot be changed meaningfully after creation, so getting it wrong means
rebuilding. `prov_obs01.py` sets BIOS explicitly and says why.

**EVC does not explain a missing instruction set.** Guests here report no
AVX2, which looks like an EVC baseline mask. `evc_check.sh` proves otherwise:
the hosts are Sandy Bridge and Ivy Bridge (E5-2680 v1 and v2), and AVX2
arrived with Haswell. The instruction set is physically absent, so no
cluster setting can expose it. This matters for capacity planning: it rules
out CPU inference on this hardware regardless of how much RAM is free.
`bottleneck_probe.sh` measured ~6.7 GB/s memory bandwidth, roughly 140x
slower than a discrete GPU, which is the other half of that conclusion.

## Credentials

Every script reads vCenter credentials from a `0600` file outside the repo
and passes them to `govc` through the environment. There are no credentials
in these files, and there never were: that is the pattern being shown, not
an artifact of sanitizing for publication.

The automation account is bound to a custom role whose mutating permissions
are scoped to a single VM folder, so a bug in a provisioning script can
damage a sandbox rather than the estate.

## Addressing

Internal addresses are replaced with `${PLACEHOLDER}` names. The
`10.110.0.0/24` cluster subnet is left intact because it is a documented lab
range and the scripts read better with real examples.
