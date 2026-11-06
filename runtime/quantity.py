"""Resolve mint quantity without binding a specific executor.

Unspecified quantity fills to the minimum of the wallet remaining cap and the
per-transaction cap. Explicit quantities stay as requested and still cannot
exceed those caps. The trusted host reports caps; confirmation stays outside.
"""
from .common import uint


POLICIES = {'fill_to_max', 'require_explicit'}
MODES = {'explicit', 'fill_to_max'}
REQUIRED_CAPS = ('wallet_remaining', 'per_tx')
OPTIONAL_CAPS = ('supply_remaining', 'balance_limited')


def normalize_quantity(raw, quantity_policy='fill_to_max'):
    if quantity_policy not in POLICIES:
        raise ValueError('invalid quantity policy')
    if 'quantity' not in raw or raw['quantity'] is None or raw['quantity'] == 'max':
        if quantity_policy != 'fill_to_max':
            raise ValueError('explicit quantity required')
        return None, 'fill_to_max'
    quantity = uint(raw['quantity'])
    if int(quantity) <= 0:
        raise ValueError('positive explicit quantity required')
    return quantity, 'explicit'


def installer_share_cap(value):
    if value is None:
        return None
    if isinstance(value, str):
        parsed = int(uint(value))
    elif type(value) is int and value >= 0:
        parsed = value
    else:
        raise ValueError('invalid per-transaction share cap')
    return parsed


def cap_bounds(caps, installer_per_tx=None):
    if not isinstance(caps, dict):
        raise ValueError('mint caps must be an object')
    bounds = []
    for key in REQUIRED_CAPS:
        if caps.get(key) is None:
            raise ValueError('wallet and per-tx mint caps required')
        bounds.append(int(uint(caps[key])))
    for key in OPTIONAL_CAPS:
        if caps.get(key) is not None:
            bounds.append(int(uint(caps[key])))
    installer = installer_share_cap(installer_per_tx)
    if installer is not None:
        bounds.append(installer)
    return bounds


def resolve_mint_quantity(intent, caps, installer_per_tx=None, mode='explicit'):
    """Return the decimal quantity string allowed for this order."""
    if mode not in MODES:
        raise ValueError('unknown quantity mode')
    maximum = min(cap_bounds(caps, installer_per_tx))
    if maximum <= 0:
        raise ValueError('no remaining mint quantity under wallet or per-tx caps')
    if mode == 'fill_to_max':
        return str(maximum)
    wanted = int(uint(intent['quantity']))
    if wanted <= 0:
        raise ValueError('positive explicit quantity required')
    if wanted > maximum:
        raise ValueError('quantity exceeds wallet or per-tx cap')
    return str(wanted)
