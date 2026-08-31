"""Read Vulkan physical device types through the public Vulkan 1.0 API."""

import ctypes
import os
from pathlib import Path


def device_types() -> dict[str, int]:
    """Return physical device names and types (1 integrated, 2 discrete)."""
    if os.name != "nt":
        return {}

    class InstanceInfo(ctypes.Structure):
        _fields_ = [
            ("sType", ctypes.c_uint32), ("pNext", ctypes.c_void_p),
            ("flags", ctypes.c_uint32), ("pApplicationInfo", ctypes.c_void_p),
            ("enabledLayerCount", ctypes.c_uint32), ("ppEnabledLayerNames", ctypes.c_void_p),
            ("enabledExtensionCount", ctypes.c_uint32), ("ppEnabledExtensionNames", ctypes.c_void_p),
        ]

    # VkPhysicalDeviceProperties has this stable Vulkan 1.0 prefix. Reserve more
    # than its remaining limits/sparse-properties fields; no extensions are used.
    class Properties(ctypes.Structure):
        _fields_ = [
            ("apiVersion", ctypes.c_uint32), ("driverVersion", ctypes.c_uint32),
            ("vendorID", ctypes.c_uint32), ("deviceID", ctypes.c_uint32),
            ("deviceType", ctypes.c_uint32), ("deviceName", ctypes.c_char * 256),
            ("remaining", ctypes.c_byte * 4096),
        ]

    instance = ctypes.c_void_p()
    try:
        loader = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "vulkan-1.dll"
        vk = ctypes.WinDLL(str(loader))
        vk.vkCreateInstance.argtypes = [ctypes.POINTER(InstanceInfo), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        vk.vkCreateInstance.restype = ctypes.c_int32
        vk.vkDestroyInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        vk.vkDestroyInstance.restype = None
        vk.vkEnumeratePhysicalDevices.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_void_p)]
        vk.vkEnumeratePhysicalDevices.restype = ctypes.c_int32
        vk.vkGetPhysicalDeviceProperties.argtypes = [ctypes.c_void_p, ctypes.POINTER(Properties)]
        vk.vkGetPhysicalDeviceProperties.restype = None
        if vk.vkCreateInstance(ctypes.byref(InstanceInfo(sType=1)), None, ctypes.byref(instance)) != 0:
            return {}
        count = ctypes.c_uint32()
        if vk.vkEnumeratePhysicalDevices(instance, ctypes.byref(count), None) != 0 or not 0 < count.value <= 64:
            return {}
        devices = (ctypes.c_void_p * count.value)()
        if vk.vkEnumeratePhysicalDevices(instance, ctypes.byref(count), devices) != 0:
            return {}
        result = {}
        for device in devices[:count.value]:
            properties = Properties()
            vk.vkGetPhysicalDeviceProperties(device, ctypes.byref(properties))
            result[properties.deviceName.decode("utf-8", errors="replace")] = properties.deviceType
        return result
    except (OSError, AttributeError, ValueError):
        return {}
    finally:
        if instance.value:
            vk.vkDestroyInstance(instance, None)
