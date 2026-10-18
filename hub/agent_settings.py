"""Per-device settings the hub pushes to apps with switchable features (the Android app): which features
run, and which look the app wears. Settings live on the host; devices only report what they have."""

DEVICE_THEMES = ("auto", "material", "cogitator")


class AgentSettings:
    def __init__(self, config):
        self.config = config
        self.data = config.data.setdefault("agent_settings", {})        # agent id -> {features, titles, version}
        self.appearance = config.data.setdefault("appearance", {"pc_theme": "plasma", "device_theme": "auto"})

    def seen(self, agent_id, features, version):
        """An app said hello with its feature list [{id, title, default}]: remember it, keep choices."""
        entry = self.data.setdefault(agent_id, {"features": {}})
        entry["titles"] = {f["id"]: str(f.get("title", f["id"])) for f in features if "id" in f}
        for f in features:
            entry["features"].setdefault(f["id"], bool(f.get("default", f["id"] == "clipboard")))
        entry["version"] = version
        self.config.save()

    def set_settings(self, agent_id, feature, settings):
        """A feature's own settings for one device (e.g. the notification app filter), replaced whole."""
        entry = self.data.get(agent_id)
        if entry is None or feature not in entry.get("titles", {}):
            raise ValueError("unknown device or feature")
        if not isinstance(settings, dict):
            raise ValueError("settings must be an object")
        entry.setdefault("settings", {})[feature] = settings
        self.config.save()

    def set_catalog(self, agent_id, feature, items):
        """What a device offers a feature's settings to choose from (e.g. its apps), for the GUI."""
        entry = self.data.get(agent_id)
        if entry is None:
            return
        entry.setdefault("catalog", {})[feature] = items
        self.config.save()

    def configured(self):
        return list(self.data)

    def set_feature(self, agent_id, feature, enabled):
        entry = self.data.get(agent_id)
        if entry is None or feature not in entry.get("titles", {}):
            raise ValueError("unknown device or feature")
        entry["features"][feature] = enabled
        self.config.save()

    def set_appearance(self, pc_theme=None, device_theme=None):
        if pc_theme is not None:
            self.appearance["pc_theme"] = str(pc_theme)
        if device_theme is not None:
            if device_theme not in DEVICE_THEMES:
                raise ValueError(f"device theme must be one of {DEVICE_THEMES}")
            self.appearance["device_theme"] = device_theme
        self.config.save()

    def device_theme(self):
        """"auto" follows the PC GUI: Cogitator there means Cogitator on devices, anything else Material."""
        choice = self.appearance.get("device_theme", "auto")
        if choice == "auto":
            return "cogitator" if self.appearance.get("pc_theme") == "cogitator" else "material"
        return choice

    def config_message(self, agent_id):
        entry = self.data.get(agent_id, {})
        return {"type": "config", "theme": self.device_theme(), "features": dict(entry.get("features", {})),
                "settings": dict(entry.get("settings", {}))}

    def summary(self, agent_id):
        """For the hub state: [{id, title, enabled}] and the app version, or None for agents without features."""
        entry = self.data.get(agent_id)
        if entry is None:
            return None
        return {"version": entry.get("version"),
                "features": [{"id": f, "title": t, "enabled": entry["features"].get(f, False),
                              "settings": entry.get("settings", {}).get(f, {}),
                              "catalog": entry.get("catalog", {}).get(f, [])}
                             for f, t in entry.get("titles", {}).items()]}
