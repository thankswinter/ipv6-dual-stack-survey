from dataclasses import dataclass

from app.core.models import Vendor


@dataclass(frozen=True)
class CliTemplate:
    arp_command: str
    ipv6_neighbor_command: str
    disable_paging_command: str
    prompt_patterns: tuple[str, ...]


# 通用 CLI 模板；同厂商不同型号通常共用 display 命令族
HUAWEI_DEFAULT = CliTemplate(
    arp_command="display arp all",
    ipv6_neighbor_command="display ipv6 neighbors",
    disable_paging_command="screen-length 0 temporary",
    prompt_patterns=("<HUAWEI>", "[~", "[*", "<S12700", "-S12700", "-EI>", "-AC>"),
)

HUAWEI_S_SWITCH = CliTemplate(
    arp_command="display arp",
    ipv6_neighbor_command="display ipv6 neighbors verbose",
    disable_paging_command="screen-length 0 temporary",
    prompt_patterns=("<HUAWEI>", "[~", "[*", "<S12700", "-S12700", "-EI>", "-AC>"),
)

H3C_DEFAULT = CliTemplate(
    arp_command="display arp",
    ipv6_neighbor_command="display ipv6 neighbors",
    disable_paging_command="screen-length disable",
    prompt_patterns=("<H3C>", "[H3C-", "<"),
)

H3C_COMWARE7 = CliTemplate(
    arp_command="display arp all",
    ipv6_neighbor_command="display ipv6 neighbors verbose",
    disable_paging_command="screen-length disable",
    prompt_patterns=("<H3C>", "[H3C-", "<"),
)


DEVICE_CLI_MAP: dict[tuple[Vendor, str], CliTemplate] = {
    # Huawei
    (Vendor.HUAWEI, "S12700"): HUAWEI_DEFAULT,
    (Vendor.HUAWEI, "S9700"): HUAWEI_DEFAULT,
    (Vendor.HUAWEI, "S7700"): HUAWEI_DEFAULT,
    (Vendor.HUAWEI, "S6700"): HUAWEI_DEFAULT,
    (Vendor.HUAWEI, "S5735"): HUAWEI_S_SWITCH,
    (Vendor.HUAWEI, "S5732"): HUAWEI_S_SWITCH,
    (Vendor.HUAWEI, "S5720"): HUAWEI_S_SWITCH,
    (Vendor.HUAWEI, "S5700"): HUAWEI_S_SWITCH,
    (Vendor.HUAWEI, "CE6857"): HUAWEI_DEFAULT,
    (Vendor.HUAWEI, "CE6881"): HUAWEI_DEFAULT,
    (Vendor.HUAWEI, "CE12800"): HUAWEI_DEFAULT,
    # H3C
    (Vendor.H3C, "S12500"): H3C_DEFAULT,
    (Vendor.H3C, "S10500"): H3C_DEFAULT,
    (Vendor.H3C, "S7500E"): H3C_DEFAULT,
    (Vendor.H3C, "S6800"): H3C_DEFAULT,
    (Vendor.H3C, "S6520X"): H3C_COMWARE7,
    (Vendor.H3C, "S6520"): H3C_COMWARE7,
    (Vendor.H3C, "S5820V2"): H3C_DEFAULT,
    (Vendor.H3C, "S5130"): H3C_DEFAULT,
    (Vendor.H3C, "S5120"): H3C_DEFAULT,
    (Vendor.H3C, "S5560X"): H3C_COMWARE7,
}


DEVICE_CATALOG: dict[Vendor, list[dict[str, str]]] = {
    Vendor.HUAWEI: [
        {"model": "S12700", "description": "华为 S12700 系列核心交换机"},
        {"model": "S9700", "description": "华为 S9700 系列核心交换机"},
        {"model": "S7700", "description": "华为 S7700 系列核心交换机"},
        {"model": "S6700", "description": "华为 S6700 系列汇聚交换机"},
        {"model": "S5735", "description": "华为 S5735 系列接入交换机"},
        {"model": "S5732", "description": "华为 S5732 系列接入交换机"},
        {"model": "S5720", "description": "华为 S5720 系列接入交换机"},
        {"model": "S5700", "description": "华为 S5700 系列接入交换机"},
        {"model": "CE6857", "description": "华为 CE6857 数据中心交换机"},
        {"model": "CE6881", "description": "华为 CE6881 数据中心交换机"},
        {"model": "CE12800", "description": "华为 CE12800 系列核心交换机"},
    ],
    Vendor.H3C: [
        {"model": "S12500", "description": "H3C S12500 系列核心交换机"},
        {"model": "S10500", "description": "H3C S10500 系列核心交换机"},
        {"model": "S7500E", "description": "H3C S7500E 系列核心交换机"},
        {"model": "S6800", "description": "H3C S6800 系列汇聚交换机"},
        {"model": "S6520X", "description": "H3C S6520X 系列接入交换机 (Comware V7)"},
        {"model": "S6520", "description": "H3C S6520 系列接入交换机 (Comware V7)"},
        {"model": "S5820V2", "description": "H3C S5820V2 系列接入交换机"},
        {"model": "S5130", "description": "H3C S5130 系列接入交换机"},
        {"model": "S5120", "description": "H3C S5120 系列接入交换机"},
        {"model": "S5560X", "description": "H3C S5560X 系列接入交换机 (Comware V7)"},
    ],
}


def get_cli_template(vendor: Vendor, model: str) -> CliTemplate:
    key = (vendor, model.upper())
    if key in DEVICE_CLI_MAP:
        return DEVICE_CLI_MAP[key]

    defaults = {
        Vendor.HUAWEI: HUAWEI_DEFAULT,
        Vendor.H3C: H3C_DEFAULT,
    }
    return defaults[vendor]
