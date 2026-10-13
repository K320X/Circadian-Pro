"""Utility functions for HA core."""
# Modified for Circadian Pro. See NOTICE and LICENSE-APACHE-2.0.txt.

import logging
from collections.abc import Awaitable, Callable, Iterable
from copy import deepcopy
from inspect import isawaitable

from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.core import Context, HassJobType, HomeAssistant, ServiceCall, ServiceResponse
from homeassistant.exceptions import Unauthorized, UnknownUser
from homeassistant.helpers import device_registry, entity_registry
from homeassistant.util.read_only_dict import ReadOnlyDict

from .adaptation_utils import ServiceData

_LOGGER = logging.getLogger(__name__)

ServiceCallPostProcessor = Callable[[bool], Awaitable[None] | None]


async def async_check_entity_control(
    hass: HomeAssistant,
    context: Context,
    entity_ids: Iterable[str],
    *,
    filter_allowed: bool = False,
) -> list[str]:
    """Validate the actor before any integration-owned control side effects.

    Home Assistant fires call_service before its entity handler checks access.
    For an 'all' target HA filters inaccessible entities instead of rejecting
    the entire action; filter_allowed preserves that behavior for our listeners.
    """
    entities = list(dict.fromkeys(entity_ids))
    if not (user_id := getattr(context, "user_id", None)):
        return entities
    user = await hass.auth.async_get_user(user_id)
    if user is None:
        raise UnknownUser(context=context)
    if user.is_admin:
        return entities
    allowed = []
    for entity_id in entities:
        if user.permissions.check_entity(entity_id, POLICY_CONTROL):
            allowed.append(entity_id)
        elif not filter_allowed:
            raise Unauthorized(context=context, entity_id=entity_id, permission=POLICY_CONTROL)
    return allowed


def area_entities(hass: HomeAssistant, area_id: str):
    """Get all entities linked to an area."""
    ent_reg = entity_registry.async_get(hass)
    entity_ids = [
        entry.entity_id
        for entry in entity_registry.async_entries_for_area(ent_reg, area_id)
    ]
    dev_reg = device_registry.async_get(hass)
    entity_ids.extend(
        [
            entity.entity_id
            for device in device_registry.async_entries_for_area(dev_reg, area_id)
            for entity in entity_registry.async_entries_for_device(ent_reg, device.id)
            if entity.area_id is None
        ],
    )
    return entity_ids


def setup_service_call_interceptor(
    hass: HomeAssistant,
    domain: str,
    service: str,
    intercept_func: Callable[
        [ServiceCall, ServiceData],
        Awaitable[ServiceCallPostProcessor | None] | ServiceCallPostProcessor | None,
    ],
) -> Callable[[], None]:
    """Inject a function into a registered service call to preprocess service data.

    The injected interceptor function receives the service call and a writeable data dictionary
    (the data of the service call is read-only) before the service call is executed.
    """
    try:
        # HACK: Access protected attribute of HA service registry.
        # This is necessary to replace a registered service handler with our
        # proxy handler to intercept calls.
        registered_services = (
            hass.services._services  # pylint: disable=protected-access  # type: ignore[attr-defined]
        )
    except AttributeError as error:
        msg = (
            "Intercept failed because registered services are no longer"
            " accessible (internal API may have changed)"
        )
        raise RuntimeError(msg) from error

    if domain not in registered_services or service not in registered_services[domain]:
        msg = f"Intercept failed because service {domain}.{service} is not registered"
        raise RuntimeError(msg)

    existing_service = registered_services[domain][service]
    active = True

    async def service_func_proxy(call: ServiceCall) -> ServiceResponse:
        post_processor = None
        if active:
            try:
                # Copy nested params too: a failed interceptor must not leak
                # partial edits into the original service call.
                data = deepcopy(dict(call.data))
                result = intercept_func(call, data)
                if isawaitable(result):
                    result = await result
                if result is not None and not callable(result):
                    raise TypeError("A service interceptor must return a callback or None")
                post_processor = result
                call.data = ReadOnlyDict(data)
            except Exception:
                # Avoid breaking light.turn_on when interception fails.
                _LOGGER.exception(
                    "Error for call '%s' in service_func_proxy",
                    call.data,
                )
        # Call original service handler with processed data
        async def finish(success: bool) -> None:
            if post_processor is None:
                return
            try:
                result = post_processor(success)
                if isawaitable(result):
                    await result
            except Exception:
                _LOGGER.exception("Could not finalize intercepted service %s.%s", domain, service)

        target = existing_service.job.target
        try:
            if existing_service.job.job_type is HassJobType.Coroutinefunction:
                response = await target(call)
            elif existing_service.job.job_type is HassJobType.Callback:
                response = target(call)
            else:
                response = await hass.async_add_executor_job(target, call)
        except BaseException:
            await finish(False)
            raise  # Preserve the original handler's error or cancellation.
        await finish(True)
        return response

    hass.services.async_register(
        domain,
        service,
        service_func_proxy,
        existing_service.schema,
        supports_response=existing_service.supports_response,
        description_placeholders=existing_service.description_placeholders,
    )

    def remove() -> None:
        nonlocal active
        if not active:
            return
        active = False
        current_service = registered_services.get(domain, {}).get(service)
        if current_service is None or current_service.job.target is not service_func_proxy:
            # A later interceptor may wrap ours. Leave its handler installed;
            # our inactive proxy remains a pass-through if its wrapper calls it.
            return
        # Remove the interceptor by reinstalling the original service handler
        hass.services.async_register(
            domain,
            service,
            existing_service.job.target,
            existing_service.schema,
            supports_response=existing_service.supports_response,
            job_type=existing_service.job.job_type,
            description_placeholders=existing_service.description_placeholders,
        )

    return remove
