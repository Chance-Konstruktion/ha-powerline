                    language, "adapter_new", name=name, mac=alert["mac"]
                )
                notification_id = f"powerline_new_{alert['mac']}"
            elif kind == "consumer_correlation":
                source = self._adapter_name(alert["source"])
                destination = self._adapter_name(alert["destination"])
                text = alert_notification_text(
                    language,
                    "consumer_correlation",
                    source=source,
                    destination=destination,
                    sensor_name=alert.get(
                        "sensor_name", alert.get("sensor_id", "sensor")
                    ),
                    threshold_w=alert.get("threshold_w", 500),
                    drop_percent=alert.get("drop_percent", 0),
                    baseline_rate=alert.get("baseline_rate", 0),
                    active_rate=alert.get("active_rate", 0),
                    days_observed=alert.get("days_observed", 0),
                )
                sensor_token = str(alert.get("sensor_id", "sensor")).replace(
                    ".", "_"
                )
                notification_id = (
                    f"powerline_consumer_{sensor_token}_"
                    f"{alert['source']}_{alert['destination']}"
                )
            else:
                cause = alert.get("cause")
                source = self._adapter_name(alert["source"])
                destination = self._adapter_name(alert["destination"])
                values = {
                    "source": source,
                    "destination": destination,
                    "average": alert.get("average", 0),
                    "peer_average": alert.get("peer_average", 0),
                    "tx": alert.get("tx", 0),
                    "rx": alert.get("rx", 0),
                }
                text = alert_notification_text(language, cause, **values)
                notification_id = (
