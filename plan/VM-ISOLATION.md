# VM isolation per bot deferred proposal

Recorded: 2026-10-24.

**Owner decision:** keep this proposal for later, after completing the existing application roadmap. VM migration is deferred beyond the complete roadmap, including the inserted Phase 6A mobile use, and its acceptance gates. Revisit it with the owner at that point; roadmap completion does not automatically start migration. No VM technology, guest privilege policy or migration is approved or implemented.

The current design remains one managed container and one persistent Hermes runtime per bot. The evaluation-before-backlog-fixes decision remains in force. This deferral applies to VM isolation; mobile use is scheduled separately in Phase 6A after Phase 6 and before the unchanged Phase 7. Hermes source must remain unmodified.

## Goal and proposed direction

Give each persistent teammate a private computer with stronger isolation and more freedom to install software, run services and maintain development environments. The research recommendation is one persistent Linux VM per bot with a writable OS and private storage. Keep Hermbot's application services, infrastructure supervisor, coordination, connector credentials and authoritative provider receipts outside the guest.

Isolation and freedom are separate choices. Moving the current restricted container into a private VM strengthens the outer boundary while preserving most existing restrictions. Granting guest administration provides broader freedom but changes which guest-side controls and reports Hermbot can trust.

| Option | Benefit | Tradeoff |
|---|---|---|
| Current container inside a private VM | Preserve packaging; add a per-bot kernel boundary | Little additional OS freedom without permission changes |
| VM-backed containers, such as Kata or Apple Container | Keep OCI packaging with VM isolation | Workload restrictions and guest administration depend on the implementation; not a drop-in supervisor replacement |
| Persistent Linux VM with Hermes and writable OS | Full guest environment, services and persistent installations | More OS maintenance, resource use and recovery work |
| macOS VM | Native macOS applications and tooling | Platform-specific requirements and capacity; not the default for every bot |

## Benefits

- A separate guest kernel makes broader internal permissions more defensible than increasing access to a shared host kernel. Hypervisor configuration, exposed interfaces and patching still determine actual isolation.
- Installed software, browser profiles, services and OS settings can survive Hermes upgrades. Runtime configuration changes should not require replacing the whole computer.
- Guest corruption, installation failures and guest kernel crashes can be recovered independently. Shared-host resource exhaustion remains possible.
- Snapshots can checkpoint useful machine state before substantial changes, including memory where supported. They cannot reverse external effects.
- Guest-local Docker and development services can operate without the physical host's Docker socket. Ordinary containers inside a VM do not require nested virtualization.

## Costs and unresolved design requirements

1. **Mutable OS maintenance:** distinguish the base image, managed runtime software, bot-installed packages and persistent user data. Define updates and recovery without losing useful installations or preserving broken systems indefinitely.
2. **Capacity:** guest kernels and services add overhead; browsers, desktops and emulators may dominate it. Measure actual idle/active memory, CPU, disk growth, startup and interactive responsiveness. Minimal microVM benchmark figures do not describe a complete working desktop. Enforce limits outside the guest.
3. **Guest root:** unrestricted guest root can modify plugins, adapters and logs, or start background processes. Guest-side protected directories cannot then be described as tamperproof. Infrastructure authority and definitive external receipts must remain outside the guest.
4. **Takeover:** current tool admission coordinates cooperating invocations. VMs do not guarantee all unrestricted guest-root or background activity stops during owner control. Define the intended guarantee and enforcement; pausing the whole VM also freezes the owner's desktop.
5. **Authorized access:** a VM does not prevent misuse of browser sessions or available credentials. Preserve backend grants, account/resource permissions and action identity; explicitly assess the current coverage limit for independently authenticated native actions.
6. **Snapshot reconciliation:** restoring a VM does not undo a Slack message, email or other provider mutation. Backend receipts remain authoritative. Reconcile restored work before continuation or retries; saved process state does not guarantee remote connections or credentials remain valid.
7. **Cloning:** provision fresh bot/machine identities and credentials. A logged-in bot snapshot is not a generic template. Snapshot duplication can copy cached identifiers and cryptographic state; keep explicit selection of copied knowledge and files.
8. **Routines:** suspended guests cannot execute Hermes's native timer. Introduce an external wake arrangement if suspension is selected, preserving missed-run, overlap and authorization behavior.
9. **Networking and host access:** enforce guest separation and management boundaries outside the guest. Avoid broad host-home mounts, forwarded SSH agents and host-management credentials. VM isolation does not neutralize access deliberately exposed through these paths.
10. **Mobile:** an accelerated Android emulator inside a bot VM can require nested virtualization. Validate host hardware, hypervisor, guest kernel and device runtime together. Separately hosted devices remain an option. Redroid has distinct kernel requirements and is not an automatic substitute.

VM migration does not itself fix interpretation, coordination, artifact delivery or outcome-acceptance defects. Existing evaluations and the issue backlog retain their own completion requirements.

## Candidate technologies

- **Apple silicon Mac:** assess a full Linux VM through Apple Virtualization.framework; Lume is one management candidate. Apple Container is an alternative when OCI packaging matters more than a conventional guest environment. Validate supervisor integration, networking, persistence and streaming.
- **Linux:** assess QEMU/KVM with libvirt for full computer environments and broad device support.
- **Smaller environments:** retain Firecracker and Kata as candidates. Firecracker's Linux/KVM requirements and minimal device model must be checked against desktop, graphics and mobile requirements.
- **Nested virtualization on Mac:** Apple documents M3-or-later hardware support, and Apple Container requires a suitable guest kernel. This qualifies the earlier discussion of the standard Docker Desktop limitation, without proving compatibility on this machine.

## Revisit and acceptance

After the current roadmap is complete, reassess actual workloads and current platform support with the owner. Select one pilot rather than building a generalized virtualization platform in advance. One VM per persistent teammate does not require a full persistent VM for every temporary delegated task.

The pilot must prove useful package installation under the selected privilege policy; desktop/browser and installed-software persistence across runtime updates; host-enforced lifecycle/resource limits; independent bot data and scoped application access; honest takeover behavior; backup/restore; fresh-identity cloning; external-effect reconciliation; routine wake/restart behavior; and resource use under representative concurrent work. Validate mobile acceleration separately if a device is placed inside the guest.

Preserve recoverable copies of private volumes, histories, configuration and receipts before migration. Broaden rollout only after acceptance and code review. The October 24 research inspected documentation and code; no VM was provisioned or benchmarked.

## Research references

Recheck release-specific behavior before adoption. Main-branch documentation does not establish installed compatibility.

- [Docker security](https://docs.docker.com/engine/security/) — capabilities, namespaces, mounts and daemon boundaries.
- [Apple Container overview](https://github.com/apple/container/blob/main/docs/technical-overview.md) and [Kata architecture](https://github.com/kata-containers/kata-containers/blob/main/docs/design/architecture/README.md) — VM-backed container designs.
- [Firecracker production setup](https://github.com/firecracker-microvm/firecracker/blob/main/docs/prod-host-setup.md), [design](https://github.com/firecracker-microvm/firecracker/blob/main/docs/design.md) and [FAQ](https://github.com/firecracker-microvm/firecracker/blob/main/FAQ.md) — maintenance, limits, device model and host responsibilities.
- [Firecracker snapshots](https://github.com/firecracker-microvm/firecracker/blob/main/docs/snapshotting/snapshot-support.md) — restore/clone identity constraints; [E2B persistence](https://docs.e2b.dev/sandbox/persistence) — filesystem/memory pause and resume.
- [Apple Container machine](https://github.com/apple/container/blob/main/docs/container-machine.md) — isolation tradeoffs from host-home and SSH-agent integration.
- [Apple nested virtualization](https://github.com/apple/container/blob/main/docs/runtime-configuration.md#expose-virtualization-capabilities-to-a-container) — hardware and guest-kernel prerequisites.
- [Lume](https://cua.ai/docs/lume), [VM management](https://cua.ai/docs/lume/guides/manage-vms) and [QEMU](https://www.qemu.org/docs/master/system/introduction.html) — lifecycle and platform candidates.
- [Google emulator containers](https://github.com/google/android-emulator-container-scripts#requirements) and [redroid](https://github.com/remote-android/redroid-doc) — distinct Android deployment requirements.
