#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2023, Simon Dodsley (simon@purestorage.com), Jan Kodera (jkodera@purestorage.com)
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: fusion_se
version_added: '1.0.0'
short_description:  Manage storage endpoints in Pure Storage Fusion
description:
- Create or delete storage endpoints in Pure Storage Fusion.
notes:
- Supports C(check_mode).
author:
- Pure Storage Ansible Team (@sdodsley) <pure-ansible-team@purestorage.com>
options:
  name:
    description:
    - The name of the storage endpoint.
    type: str
    required: true
  display_name:
    description:
    - The human name of the storage endpoint.
    - If not provided, defaults to I(name).
    type: str
  state:
    description:
    - Define whether the storage endpoint should exist or not.
    type: str
    default: present
    choices: [ absent, present ]
  region:
    description:
    - The name of the region the availability zone is in
    type: str
    required: true
  availability_zone:
    aliases: [ az ]
    description:
    - The name of the availability zone for the storage endpoint.
    type: str
    required: true
  endpoint_type:
    description:
    - Type of the storage endpoint. Only iSCSI is available at the moment.
    type: str
    default: iscsi
    choices: [ iscsi ]
  network_interface_groups:
    description:
    - List of network interface groups to assign to the storage endpoints.
    type: list
    elements: str
  addresses:
    description:
    - List of IP addresses to be used in the subnet of the storage endpoint.
    - IP addresses must include a CIDR notation.
    - Only IPv4 is supported at the moment.
    type: list
    elements: str
  gateway:
    description:
    - Address of the subnet gateway.
    - Currently this must be provided.
    type: str
extends_documentation_fragment:
- purestorage.fusion.purestorage.fusion
"""

EXAMPLES = r"""
- name: Create new storage endpoint foo in AZ bar
  purestorage.fusion.fusion_se:
    name: foo
    availability_zone: bar
    gateway: 10.21.200.1
    region: us-west
    addresses:
      - 10.21.200.124/24
      - 10.21.200.36/24
    network_interface_groups:
      - subnet-0
    state: present
    app_id: key_name
    key_file: "az-admin-private-key.pem"

- name: Delete storage endpoint foo in AZ bar
  purestorage.fusion.fusion_se:
    name: foo
    availability_zone: bar
    region: us-west
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

from ansible_collections.purestorage.fusion.plugins.module_utils.networking import (
    is_valid_network,
)
from ansible_collections.purestorage.fusion.plugins.module_utils.errors import (
    install_fusion_exception_hook,
)
from ansible_collections.purestorage.fusion.plugins.module_utils.operations import (
    await_operation,
)


def get_nifg(module, fusion):
    """Check all Network Interface Groups"""
    nifg_api_instance = purefusion.NetworkInterfaceGroupsApi(fusion)
    for group in range(0, len(module.params["network_interface_groups"])):
        try:
            nifg_api_instance.get_network_interface_group(
                region_name=module.params["region"],
                availability_zone_name=module.params["availability_zone"],
                provider_subnet=module.params["network_interface_groups"][group],
            )
        except purefusion.rest.ApiException:
            return False
    return True


def get_az(module, fusion):
    """Availability Zone or None"""
    az_api_instance = purefusion.AvailabilityZonesApi(fusion)
    try:
        return az_api_instance.get_availability_zone(
            availability_zone_name=module.params["availability_zone"],
            region_name=module.params["region"],
        )
    except purefusion.rest.ApiException:
        return None


def get_se(module, fusion):
    """Storage Endpoint or None"""
    se_api_instance = purefusion.StorageEndpointsApi(fusion)
    try:
        return se_api_instance.get_storage_endpoint(
            region_name=module.params["region"],
            storage_endpoint_name=module.params["name"],
            availability_zone_name=module.params["availability_zone"],
        )
    except purefusion.rest.ApiException:
        return None


def create_se(module, fusion):
    """Create Storage Endpoint"""

    se_api_instance = purefusion.StorageEndpointsApi(fusion)

    changed = True

    if not module.check_mode:
        if not module.params["display_name"]:
            display_name = module.params["name"]
        else:
            display_name = module.params["display_name"]
        ifaces = []
        for address in module.params["addresses"]:
            if module.params["gateway"]:
                iface = purefusion.StorageEndpointIscsiDiscoveryInterfacePost(
                    address=address,
                    gateway=module.params["gateway"],
                    network_interface_groups=module.params["network_interface_groups"],
                )
            else:
                iface = purefusion.StorageEndpointIscsiDiscoveryInterfacePost(
                    address=address,
                    network_interface_groups=module.params["network_interface_groups"],
                )
            ifaces.append(iface)
        op = purefusion.StorageEndpointPost(
            endpoint_type=module.params["endpoint_type"],
            iscsi=purefusion.StorageEndpointIscsiPost(
                discovery_interfaces=ifaces,
            ),
            name=module.params["name"],
            display_name=display_name,
        )
        op = se_api_instance.create_storage_endpoint(
            op,
            region_name=module.params["region"],
            availability_zone_name=module.params["availability_zone"],
        )
        await_operation(module, fusion, op)

    module.exit_json(changed=changed)


def delete_se(module, fusion):
    """Delete Storage Endpoint"""
    changed = True
    se_api_instance = purefusion.StorageEndpointsApi(fusion)
    if not module.check_mode:
        op = se_api_instance.delete_storage_endpoint(
            region_name=module.params["region"],
            availability_zone_name=module.params["availability_zone"],
            storage_endpoint_name=module.params["name"],
        )
        await_operation(module, fusion, op)
    module.exit_json(changed=changed)


def update_se(module, fusion, se):
    """Update Storage Endpoint"""

    se_api_instance = purefusion.StorageEndpointsApi(fusion)
    patches = []
    if (
        module.params["display_name"]
        and module.params["display_name"] != se.display_name
    ):
        patch = purefusion.StorageEndpointPatch(
            display_name=purefusion.NullableString(module.params["display_name"]),
        )
        patches.append(patch)

    if not module.check_mode:
        for patch in patches:
            op = se_api_instance.update_storage_endpoint(
                patch,
                region_name=module.params["region"],
                availability_zone_name=module.params["availability_zone"],
                storage_endpoint_name=module.params["name"],
            )
            await_operation(module, fusion, op)

    changed = len(patches) != 0

    module.exit_json(changed=changed)


def main():
    """Main code"""
    argument_spec = fusion_argument_spec()
    argument_spec.update(
        dict(
            name=dict(type="str", required=True),
            display_name=dict(type="str"),
            region=dict(type="str", required=True),
            availability_zone=dict(type="str", required=True, aliases=["az"]),
            endpoint_type=dict(type="str", default="iscsi", choices=["iscsi"]),
            addresses=dict(type="list", elements="str"),
            gateway=dict(type="str"),
            network_interface_groups=dict(type="list", elements="str"),
            state=dict(type="str", default="present", choices=["absent", "present"]),
        )
    )

    module = AnsibleModule(argument_spec, supports_check_mode=True)
    install_fusion_exception_hook(module)

    state = module.params["state"]
    fusion = get_fusion(module)
    if not get_az(module, fusion):
        module.fail_json(
            msg="Availability Zone {0} does not exist".format(
                module.params["availability_zone"]
            )
        )
    if module.params["network_interface_groups"] and not get_nifg(module, fusion):
        module.fail_json(
            msg="Not all of the network interface groups exist in the specified AZ"
        )
    if module.params["addresses"]:
        for address in module.params["addresses"]:
            if not is_valid_network(address):
                module.fail_json(
                    msg="'{0}' is not a valid address in CIDR notation".format(address)
                )

    sendp = get_se(module, fusion)

    if state == "present" and not sendp:
        module.fail_on_missing_params(["addresses"])
        if not (module.params["addresses"]):
            module.fail_json(
                msg="At least one entry in 'addresses' is required to create new storage endpoint"
            )
        create_se(module, fusion)
    elif state == "present" and sendp:
        update_se(module, fusion, sendp)
    elif state == "absent" and sendp:
        delete_se(module, fusion)

    module.exit_json(changed=False)


if __name__ == "__main__":
    main()
