"""Optional local control for a TP-Link Kasa smart plug."""

import asyncio


async def _set_power_async(ip_address, on):
    from kasa import SmartPlug

    plug = SmartPlug(ip_address)
    await asyncio.wait_for(plug.update(), timeout=5)
    if on:
        await asyncio.wait_for(plug.turn_on(), timeout=5)
    else:
        await asyncio.wait_for(plug.turn_off(), timeout=5)
    return {
        'alias': getattr(plug, 'alias', None),
        'is_on': bool(getattr(plug, 'is_on', on)),
        'ip': ip_address,
    }


def set_power(ip_address, on):
    """Set a plug state and return a small status payload."""
    ip_address = str(ip_address or '').strip()
    if not ip_address:
        raise ValueError('TP-Link plug IP address is not configured')
    return asyncio.run(_set_power_async(ip_address, bool(on)))
