FROM nousresearch/hermes-agent@sha256:d4da4a40cd7a28aba983775d9fd31d94cbf153eeb0cb9e844d6d0f612b7c24db
USER root
RUN apt-get update && apt-get install -y --no-install-recommends tigervnc-standalone-server xfce4-panel xfwm4 xfdesktop4 xfce4-settings xfce4-terminal dbus-x11 x11-xserver-utils x11-utils xauth fonts-dejavu-core chromium xdotool && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /opt/hermbot /workspace /browser-state && chown hermes:hermes /workspace /browser-state
COPY docker/hermes_bootstrap.py /opt/hermbot/bootstrap.py
COPY docker/copy_state.py /opt/hermbot/copy_state.py
COPY docker/write_attachment.py /opt/hermbot/write_attachment.py
COPY docker/capability_filter.py /opt/hermbot/capability_filter.py
COPY docker/routine_wakeup.py /opt/hermbot/routine_wakeup.py
COPY docker/teaching_capture.py /opt/hermbot/teaching_capture.py
COPY docker/runtime_api.py /opt/hermbot/runtime_api.py
COPY docker/install_cua.py /opt/hermbot/install_cua.py
RUN /opt/hermes/.venv/bin/python /opt/hermbot/install_cua.py
COPY hermbot_runtime_gate.py /opt/hermbot/hermbot_runtime_gate.py
COPY plugins/hermbot /opt/hermbot/plugin
WORKDIR /workspace
ENV HERMES_HOME=/opt/data PYTHONPATH=/opt/hermbot PYTHONUNBUFFERED=1 HERMES_CUA_DRIVER_CMD=/opt/hermbot/cua/cua-driver
ENV HERMES_WRITE_SAFE_ROOT=/opt/data:/workspace:/browser-state:/tmp
ENTRYPOINT ["/opt/hermes/.venv/bin/python", "/opt/hermbot/bootstrap.py"]
