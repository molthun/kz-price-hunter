"""Explicit product types, not incidental RAM/disk specs of a complete device."""
import re

_PREFIX = r'^\W*(?:(?:внешний|внутренний|портативный|external|internal)\s+)?'
_RAM = re.compile(r'^\W*(?:(?:комплект|набор)\s+)?(?:оперативн\w*\s+памят\w*|модул[ьи]\s+(?:оперативной\s+)?памяти|(?:so[ -]?)?dimm|[ur]dimm|ram\b|memory\s+(?:module|kit))', re.I)
_SSD = re.compile(r'\bssd\b|\bтвердотельный\s+накопитель\b', re.I)
_HDD = re.compile(r'\bhdd\b|\bж[её]стк(?:ий|ие)\s+диск\w*\b|\bвинчестер\b', re.I)
_REMOVABLE = re.compile(_PREFIX + r'(?:(?:usb[ -]+)?фл[еэ]ш(?:ка|ки|[ -]?(?:накопитель|память|диск))|карта\s+памяти|(?:usb[ -]+)?flash[ -]+(?:drive|memory|карта)|(?:micro\s*sd|sdhc|sdxc)\b)', re.I)
_DEVICE = re.compile(r'\b(?:ноутбук\w*|laptop|macbook|компьютер\w*|моноблок\w*|системный\s+блок|видеокарт\w*|материнск\w*|motherboard|процессор\w*|радиатор\w*|кулер\w*|адаптер\w*|переходник\w*|корпус\w*|кабел\w*|кабель|бокс\w*|контроллер\w*|док[- ]станци\w*)\b', re.I)
_DIMM = re.compile(r'\b(?:[ur]?dimm|so[ -]?dimm)\b', re.I)
_DDR = re.compile(r'\bddr\s*[345]\b', re.I)


def storage_type(title):
    text = title or ''
    if _RAM.search(text):
        return 'ram'
    if _REMOVABLE.search(text):
        return 'removable_storage'
    # Incidental specs and accessory compatibility do not identify the sold item.
    primary = re.split(r"\b(?:для|for)\b",text,maxsplit=1,flags=re.I)[0]
    disk_head = re.match(_PREFIX + r'(?:ssd\b|hdd\b|ж[её]стк(?:ий|ие)\s+диск|накопитель\s+(?:ssd|hdd)\b|твердотельный\s+накопитель)',primary,re.I)
    if disk_head:
        if _SSD.search(primary):
            return 'storage'
        if _HDD.search(primary):
            return 'hdd'
    if _DEVICE.search(primary):
        return None
    if _SSD.search(primary):
        return 'storage'
    if _HDD.search(primary):
        return 'hdd'
    if _DIMM.search(text) and _DDR.search(text):
        return 'ram'
    return None
