FROM secretclubhouse/thermostat-base:latest

RUN cd /app
WORKDIR /app
COPY main.py devices.py ./
ENV PATH="/app/venv/bin:$PATH"
CMD [ "python", "-u", "main.py"]

