FROM docker.io/secretclubhouse/thermostat-base:latest as app
COPY --from=base /app/venv /app/venv
WORKDIR app
COPY main.py devices.py ./
ENV PATH="/app/venv/bin:$PATH"
CMD [ "python", "-u", "main.py"]

