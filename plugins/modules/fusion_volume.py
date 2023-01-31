#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2022, Simon Dodsley (simon@purestorage.com)
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: fusion_volume
version_added: '1.0.0'
short_description:  Manage volumes in Pure Storage Fusion
description:
- Create, update or delete a volume in Pure Storage Fusion.
author:
- Pure Storage Ansible Team (@sdodsley) <pure-ansible-team@purestorage.com>
notes:
- Supports C(check mode).
options:
  name:
    description:
    - The name of the volume.
    type: str
    required: true
  display_name:
    description:
    - The human name of the volume.
    - If not provided, defaults to I(name).
    type: str
  state:
    description:
    - Define whether the volume should exist or not.
    type: str
    default: present
    choices: [ absent, present ]
  tenant:
    description:
    - The name of the tenant.
    type: str
    required: true
  tenant_space:
    description:
    - The name of the tenant space.
    type: str
    required: true
  eradicate:
    description:
    - Define whether to eradicate the volume on delete or leave in trash.
    type: bool
    default: 'no'
  size:
    description:
    - Volume size in M, G, T or P units.
    type: str
  storage_class:
    description:
    - The name of the storage class.
    type: str
  placement_group:
    description:
    - The name of the plcement group.
    type: str
  protection_policy:
    description:
    - The name of the protection policy.
    type: str
  host_access_policies:
    description:
    - A list of host access policies to connect the volume to.
    type: list
    elements: str
  rename:
    description:
    - New name for volume.
    type: str
extends_documentation_fragment:
- purestorage.fusion.purestorage.fusion
"""

EXAMPLES = r"""
- name: Create new volume named foo in storage_class fred
  purestorage.fusion.fusion_volume:
    name: foo
    storage_class: fred
    size: 1T
    tenant: test
    tenant_space: space_1
    state: present
    app_id: key_name
    key_file: "az-admin-private-key.pem"

- name: Extend the size of an existing volume named foo
  purestorage.fusion.fusion_volume:
    name: foo
    size: 2T
    tenant: test
    tenant_space: space_1
    state: present
    app_id: key_name
    key_file: "az-admin-private-key.pem"

- name: Rename volume named foo to bar
  purestorage.fusion.fusion_volume:
    name: foo
    rename: bar
    tenant: test
    tenant_space: space_1
    state: absent
    app_id: key_name
    key_file: "az-admin-private-key.pem"

- name: Delete volume named foo
  purestorage.fusion.fusion_volume:
    name: foo
    tenant: test
    tenant_space: space_1
    state: absent
    app_id: key_name
    key_file: "az-admin-private-key.pem"
"""

RETURN = r"""
"""

HAS_FUSION = True
try:
    import fusion as purefusion
except ImportError:
    HAS_FUSION = False

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.purestorage.fusion.plugins.module_utils.fusion import (
    get_fusion,
    fusion_argument_spec,
)
from ansible_collections.purestorage.fusion.plugins.module_utils.parsing import (
    parse_number_with_suffix,
)
from ansible_collections.purestorage.fusion.plugins.module_utils.operations import (
    await_operation,
)


def _check_host_access_policies(module, fusion):
    current_haps = []
    hap_api_instance = purefusion.HostAccessPoliciesApi(fusion)
    haps = hap_api_instance.list_host_access_policies()
    for host in range(0, len(haps.items)):
        current_haps.append(haps.items[host].name)
    if not (set(module.params["host_access_policies"]).issubset(set(current_haps))):
        module.fail_json(
            msg="At least one of the specified host access policies does not currently exist"
        )


def _check_target_volume(module, fusion):
    volume_api_instance = purefusion.VolumesApi(fusion)
    try:
        op_id = volume_api_instance.get_volume(
            tenant_name=module.params["tenant"],
            tenant_space_name=module.params["tenant_space"],
            volume_name=module.params["rename"],
        )
        return True
    except purefusion.rest.ApiException:
        return False

def get_volume(module, fusion):
    """Return Volume or None"""
    volume_api_instance = purefusion.VolumesApi(fusion)
    try:
        return volume_api_instance.get_volume(
            tenant_name=module.params["tenant"],
            tenant_space_name=module.params["tenant_space"],
            volume_name=module.params["name"],
        )
    except purefusion.rest.ApiException:
        return None


def get_sc(module, fusion):
    """Return Storage Class or None"""
    sc_api_instance = purefusion.StorageClassesApi(fusion)
    try:
        return sc_api_instance.get_storage_class(
            storage_class_name=module.params["storage_class"]
        )
    except purefusion.rest.ApiException:
        return None


def get_pg(module, fusion):
    """Return Placement Group or None"""
    pg_api_instance = purefusion.PlacementGroupsApi(fusion)
    try:
        return pg_api_instance.get_placement_group(
            tenant_name=module.params["tenant"],
            tenant_space_name=module.params["tenant_space"],
            placement_group_name=module.params["placement_group"],
        )
    except purefusion.rest.ApiException:
        return None


def get_pp(module, fusion):
    """Return Protection Policy or None"""
    pp_api_instance = purefusion.ProtectionPoliciesApi(fusion)
    try:
        return pp_api_instance.get_protection_policy(
            protection_policy_name=module.params["protection_policy"]
        )
    except purefusion.rest.ApiException:
        return None


def get_destroyed_volume(module, fusion):
    """Return Destroyed Volume or None"""
    volume_api_instance = purefusion.VolumeSnapshotsApi(fusion)
    try:
        return volume_api_instance.get_volume_snapshot(
            volume_name=module.params["name"],
            tenant_name=module.params["tenant"],
            tenant_space_name=module.params["tenant_space"],
        )
    except purefusion.rest.ApiException:
        return False


def create_volume(module, fusion):
    """Create Volume"""

    sc_api_instance = purefusion.StorageClassesApi(fusion)
    volume_api_instance = purefusion.VolumesApi(fusion)

    if not module.params["size"]:
        module.fail_json(msg="Size for a new volume must be specified")
    size = parse_number_with_suffix(module.params["size"])
    sc_size_limit = sc_api_instance.get_storage_class(
        storage_class_name=module.params["storage_class"]
    ).size_limit
    if size > sc_size_limit:
        module.fail_json(
            msg="Requested size {0} exceeds the storage class limit of {1}".format(
                module.params["size"], print_number_with_suffix(sc_size_limit)
            )
        )

    changed = True
    if not module.check_mode:
        if not module.params["display_name"]:
            display_name = module.params["name"]
        else:
            display_name = module.params["display_name"]
        try:
            volume = purefusion.VolumePost(
                size=size,
                storage_class=module.params["storage_class"],
                placement_group=module.params["placement_group"],
                name=module.params["name"],
                display_name=display_name,
            )
            volume_api_instance.create_volume(
                volume,
                tenant_name=module.params["tenant"],
                tenant_space_name=module.params["tenant_space"],
            )
        except purefusion.rest.ApiException as err:
            module.fail_json(
                msg="Volume {0} creation failed.: {1}".format(
                    module.params["name"], err
                )
            )
    if module.params["host_access_policies"]:
        volume = purefusion.VolumePatch(
            host_access_policies=purefusion.NullableString(module.params["host_access_policies"])
        )

    module.exit_json(changed=changed)


def update_volume(module, fusion):
    """Update Volume size, placement group, storage class, HAPs"""
    changed = False
    sc_api_instance = purefusion.StorageClassesApi(fusion)
    volume_api_instance = purefusion.VolumesApi(fusion)

    vol = volume_api_instance.get_volume(
        tenant_name=module.params["tenant"],
        tenant_space_name=module.params["tenant_space"],
        volume_name=module.params["name"],
    )
    haps = []
    if vol.host_access_policies:
        for host in range(0, len(vol.host_access_policies)):
            haps.append(vol.host_access_policies[host].name)
    current_vol = {
        "size": vol.size,
        "host_access_policies": list(dict.fromkeys(haps)),
        "placement_group": vol.placement_group.name,
        "protection_policy": getattr(vol.protection_policy, "name", None),
        "storage_class": vol.storage_class.name,
        "display_name": vol.display_name,
    }
    new_vol = {
        "size": vol.size,
        "host_access_policies": list(dict.fromkeys(haps)),
        "placement_group": vol.placement_group.name,
        "protection_policy": getattr(vol.protection_policy, "name", None),
        "storage_class": vol.storage_class.name,
        "display_name": vol.display_name,
    }
    if (
        module.params["storage_class"]
        and module.params["storage_class"] != current_vol["storage_class"]
    ):
        new_vol["storage_class"] = module.params["storage_class"]
    if (
        module.params["size"]
        and parse_number_with_suffix(module.params["size"]) != current_vol["size"]
    ):
        if parse_number_with_suffix(module.params["size"]) > current_vol["size"]:
            new_vol["size"] = parse_number_with_suffix(module.params["size"])
        sc_size_limit = sc_api_instance.get_storage_class(
            storage_class_name=new_vol["storage_class"]
        ).size_limit
        if new_vol["size"] > sc_size_limit:
            module.fail_json(
                msg="Volume size {0} exceeds the storage class limit of {1}".format(
                    new_vol["size"], sc_size_limit
                )
            )
    if not module.params["size"] and module.params["storage_class"]:
        sc_size_limit = sc_api_instance.get_storage_class(
            storage_class_name=new_vol["storage_class"]
        ).size_limit
        if current_vol["size"] > sc_size_limit:
            module.fail_json(
                msg="Volume size {0} exceeds the storage class limit of {1}".format(
                    new_vol["size"], sc_size_limit
                )
            )

    if (
        module.params["placement_group"]
        and module.params["placement_group"] != current_vol["placement_group"]
    ):
        new_vol["protection_group"] = module.params["placement_group"]
    if (
        module.params["protection_policy"]
        and module.params["protection_policy"] != current_vol["protection_policy"]
    ):
        new_vol["protection_policy"] = module.params["protection_policy"]
    if (
        module.params["display_name"]
        and module.params["display_name"] != current_vol["display_name"]
    ):
        new_vol["display_name"] = module.params["display_name"]

    if (new_vol != current_vol) or module.params["host_access_policies"]:
        changed = False
        if not module.check_mode:
            # PATCH is atomic so has to pass or fail, therefore only one item
            # can be changed at a time
            if new_vol["display_name"] != current_vol["display_name"]:
                volume = purefusion.VolumePatch(
                    display_name=purefusion.NullableString(new_vol["display_name"])
                )
                try:
                    res = volume_api_instance.update_volume(
                        volume,
                        volume_name=module.params["name"],
                        tenant_name=module.params["tenant"],
                        tenant_space_name=module.params["tenant_space"],
                    )
                    changed = True
                except purefusion.rest.ApiException as err:
                    module.fail_json(
                        msg="Changing display_name failed: {0}".format(err)
                    )
            if new_vol["storage_class"] != current_vol["storage_class"]:
                volume = purefusion.VolumePatch(
                    storage_class=purefusion.NullableString(new_vol["storage_class"])
                )
                try:
                    res = volume_api_instance.update_volume(
                        volume,
                        volume_name=module.params["name"],
                        tenant_name=module.params["tenant"],
                        tenant_space_name=module.params["tenant_space"],
                    )
                    changed = True
                except purefusion.rest.ApiException as err:
                    module.fail_json(
                        msg="Changing storage_class failed: {0}".format(err)
                    )
            if new_vol["size"] != current_vol["size"]:
                volume = purefusion.VolumePatch(
                    size=purefusion.NullableSize(new_vol["size"])
                )
                try:
                    res = volume_api_instance.update_volume(
                        volume,
                        volume_name=module.params["name"],
                        tenant_name=module.params["tenant"],
                        tenant_space_name=module.params["tenant_space"],
                    )
                    changed = True
                except purefusion.rest.ApiException as err:
                    module.fail_json(msg="Changing size failed: {0}".format(err))
            if new_vol["placement_group"] != current_vol["placement_group"]:
                volume = purefusion.VolumePatch(
                    placement_group=purefusion.NullableString(
                        new_vol["placement_group"]
                    )
                )
                try:
                    res = volume_api_instance.update_volume(
                        volume,
                        volume_name=module.params["name"],
                        tenant_name=module.params["tenant"],
                        tenant_space_name=module.params["tenant_space"],
                    )
                    changed = True
                except purefusion.rest.ApiException as err:
                    module.fail_json(
                        msg="Changing placement_group failed: {0}".format(err)
                    )
            if new_vol["protection_policy"] != current_vol["protection_policy"]:
                volume = purefusion.VolumePatch(
                    protection_policy=purefusion.NullableString(
                        new_vol["protection_policy"]
                    )
                )
                try:
                    volume_api_instance.update_volume(
                        volume,
                        volume_name=module.params["name"],
                        tenant_name=module.params["tenant"],
                        tenant_space_name=module.params["tenant_space"],
                    )
                    changed = True
                except purefusion.rest.ApiException as err:
                    module.fail_json(
                        msg="Changing protection_policy failed: {0}".format(err)
                    )
            if module.params["host_access_policies"]:
                if not new_vol["host_access_policies"]:
                    new_vol["host_access_policies"] = []
                for host in module.params["host_access_policies"]:
                    if module.params["state"] == "absent":
                        if new_vol["host_access_policies"]:
                            new_vol["host_access_policies"].remove(host)
                    else:
                        new_vol["host_access_policies"].append(host)
                new_vol["host_access_policies"] = list(dict.fromkeys(new_vol["host_access_policies"]))
                if new_vol["host_access_policies"] != current_vol["host_access_policies"]:
                    volume = purefusion.VolumePatch(
                        host_access_policies=purefusion.NullableString(",".join(new_vol["host_access_policies"]))
                    )
                    try:
                        volume_api_instance.update_volume(
                            volume,
                            volume_name=module.params["name"],
                            tenant_name=module.params["tenant"],
                            tenant_space_name=module.params["tenant_space"],
                        )
                        changed = True
                    except purefusion.rest.ApiException as err:
                        module.fail_json(msg="Changing host access policies failed: {0}".format(err))

    module.exit_json(changed=changed)


def delete_volume(module, fusion):
    """Delete Volume"""
    changed = True
    volume_api_instance = purefusion.VolumesApi(fusion)
    if not module.check_mode:
        try:
            volume_api_instance.delete_volume(
                volume_name=module.params["name"],
                tenant_name=module.params["tenant"],
                tenant_space_name=module.params["tenant_space"],
            )
            if module.params["eradicate"]:
                try:
                    pass
                #                    eradicate_volume(module, array)
                except Exception:
                    module.fail_json(
                        msg="Eradicate volume {0} failed.".format(module.params["name"])
                    )
        except purefusion.rest.ApiException:
            module.fail_json(
                msg="Delete volume {0} failed.".format(module.params["name"])
            )
    module.exit_json(changed=changed)


def eradicate_volume(module, array):
    """Eradicate Deleted Volume"""
    changed = True
    if not module.check_mode:
        try:
            array.eradicate_volume(module.params["name"])
        except Exception:
            module.fail_json(
                msg="Eradication of volume {0} failed".format(module.params["name"])
            )
    module.exit_json(changed=changed)


def recover_volume(module, array):
    """Recover Deleted Volume"""
    changed = True
    module.warn("Volume recovery not yet supported")
    #    if not module.check_mode:
    #        try:
    #            array.recover_volume(module.params["name"])
    #        except Exception:
    #            module.fail_json(
    #                msg="Recovery of volume {0} failed".format(module.params["name"])
    #            )
    module.exit_json(changed=changed)


def main():
    """Main code"""
    argument_spec = fusion_argument_spec()
    deprecated_hosts = dict(
        name="hosts",
        date="2023-07-26",
        collection_name="purefusion.fusion"
    )
    argument_spec.update(
        dict(
            name=dict(type="str", required=True),
            display_name=dict(type="str"),
            rename=dict(type="str"),
            tenant=dict(type="str", required=True),
            tenant_space=dict(type="str", required=True),
            placement_group=dict(type="str"),
            storage_class=dict(type="str"),
            protection_policy=dict(type="str"),
            host_access_policies=dict(type="list", elements="str", deprecated_aliases=[deprecated_hosts]),
            eradicate=dict(type="bool", default=False),
            state=dict(type="str", default="present", choices=["absent", "present"]),
            size=dict(type="str"),
        )
    )

    required_if = [
        ('state', 'present', ('storage_class', 'placement_group'), False)
    ]

    module = AnsibleModule(argument_spec, required_if=required_if, supports_check_mode=True)

    size = module.params["size"]
    state = module.params["state"]
    destroyed = False
    fusion = get_fusion(module)
    volume = get_volume(module, fusion)
    if module.params["rename"] and _check_target_volume(module, fusion):
        module.fail_json(
            msg="Taerget volume name {0} already exists".format(module.params["rename"])
        )

    if not volume and not (
        module.params["storage_class"] and module.params["placement_group"]
    ):
        module.fail_json(
            msg="`storage_class` and `placement_group` are required when creating a new volume"
        )
    if module.params["host_access_policies"]:
        _check_host_access_policies(module, fusion)

    if module.params["storage_class"] and not get_sc(module, fusion):
        module.fail_json(
            msg="Storage Class {0} does not exist".format(
                module.params["storage_class"]
            )
        )

    if module.params["placement_group"] and not get_pg(module, fusion):
        module.fail_json(
            msg="Placement Group {0} does not exist in the provide "
            "tenant and tenant name space".format(module.params["placement_group"])
        )

    if module.params["protection_policy"] and not get_pp(module, fusion):
        module.fail_json(
            msg="Protection Policy {0} does not exist".format(
                module.params["protection_policy"]
            )
        )

    #    if not volume:
    #        destroyed = get_destroyed_volume(module, fusion)
    if state == "present" and not volume and not destroyed and size:
        create_volume(module, fusion)
    elif (state == "present" and volume) or (
        state == "absent" and volume and module.params["host_access_policies"]
    ):
        update_volume(module, fusion)
    elif state == "absent" and volume and not module.params["host_access_policies"]:
        delete_volume(module, fusion)
    elif state == "absent" and destroyed:
        eradicate_volume(module, fusion)
    elif state == "present":
        if not volume and not size:
            module.fail_json(msg="Size must be specified to create a new volume")
    elif state == "absent" and not volume:
        module.exit_json(changed=False)

    module.exit_json(changed=False)


if __name__ == "__main__":
    main()
