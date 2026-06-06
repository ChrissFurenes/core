"""Support for Vanderbilt (formerly Siemens) SPC alarm systems."""

import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import voluptuous as vol
from aiohttp import ClientSession, DigestAuthMiddleware

from pyspcwebgw import SpcWebGateway
from pyspcwebgw.area import Area
from pyspcwebgw.zone import Zone

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import discovery
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

CONF_WS_URL = "ws_url"
CONF_API_URL = "api_url"

CONF_GET_USER = "get_user"
CONF_GET_PWD = "get_pwd"
CONF_PUT_USER = "put_user"
CONF_PUT_PWD = "put_pwd"
CONF_WS_USER = "ws_user"
CONF_WS_PWD = "ws_pwd"

DOMAIN = "spc"
DATA_API = "spc_api"

SIGNAL_UPDATE_ALARM = "spc_update_alarm_{}"
SIGNAL_UPDATE_SENSOR = "spc_update_sensor_{}"

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_API_URL): cv.string,
                vol.Required(CONF_WS_URL): cv.string,

                vol.Required(CONF_GET_USER): cv.string,
                vol.Required(CONF_GET_PWD): cv.string,

                vol.Required(CONF_PUT_USER): cv.string,
                vol.Required(CONF_PUT_PWD): cv.string,

                vol.Required(CONF_WS_USER): cv.string,
                vol.Required(CONF_WS_PWD): cv.string,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

def build_ws_url(base_url: str, username: str, password: str) -> str:
    """Add username/password query parameters to websocket URL."""
    parts = urlsplit(base_url)

    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["username"] = username
    query["password"] = password

    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query),
            parts.fragment,
        )
    )

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the SPC component."""
    spc_config = config.get(DOMAIN)
    if spc_config is None:
        _LOGGER.error("Missing SPC configuration")
        return False
    async def async_update_callback(spc_object):
        if isinstance(spc_object, Area):
            async_dispatcher_send(hass, SIGNAL_UPDATE_ALARM.format(spc_object.id))
        elif isinstance(spc_object, Zone):
            async_dispatcher_send(hass, SIGNAL_UPDATE_SENSOR.format(spc_object.id))

    get_digest_auth = DigestAuthMiddleware(login=spc_config[CONF_GET_USER],password=spc_config[CONF_GET_PWD])
    put_digest_auth = DigestAuthMiddleware(login=spc_config[CONF_PUT_USER],password=spc_config[CONF_PUT_PWD])
    get_session = ClientSession(
        middlewares=(get_digest_auth,),
    )

    put_session = ClientSession(
        middlewares=(put_digest_auth,),
    )
    ##session = aiohttp_client.async_get_clientsession(hass)
    async def close_sessions(event):
        await get_session.close()
        await put_session.close()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, close_sessions)
    ws_url = build_ws_url(
        spc_config[CONF_WS_URL],
        spc_config[CONF_WS_USER],
        spc_config[CONF_WS_PWD],
    )
    _LOGGER.debug("SPC API URL: %s", spc_config[CONF_API_URL])
    _LOGGER.debug("SPC WS URL: %s", ws_url.split("?")[0])

    spc = SpcWebGateway(
        loop=hass.loop,
        session=get_session,
        put_session=put_session,
        api_url=spc_config[CONF_API_URL],
        ws_url=ws_url,
        async_callback=async_update_callback,
    )

    hass.data[DATA_API] = spc

    if not await spc.async_load_parameters():
        _LOGGER.error("Failed to load area/zone information from SPC")
        return False

    # add sensor devices for each zone (typically motion/fire/door sensors)
    hass.async_create_task(
        discovery.async_load_platform(hass, Platform.BINARY_SENSOR, DOMAIN, {}, config)
    )

    # create a separate alarm panel for each area
    hass.async_create_task(
        discovery.async_load_platform(
            hass, Platform.ALARM_CONTROL_PANEL, DOMAIN, {}, config
        )
    )

    # start listening for incoming events over websocket
    spc.start()

    return True
