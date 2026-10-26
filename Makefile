PY ?= python3
HOSTS ?= 192.168.1.2,192.168.1.20,192.168.1.40

.PHONY: help demo discover scan agent sim mcp test clean

help:
	@echo "make demo      start the visual dashboard"
	@echo "make discover  sweep the LAN and classify devices"
	@echo "make scan      targeted port scan of key devices"
	@echo "make agent     ask the inventory agent a question"
	@echo "make sim       run a goal against the virtual endpoints"
	@echo "make test      run the standard-library test suite"
	@echo "make mcp       run the stdio MCP server"

demo:
	$(PY) viz_server.py --open

discover:
	$(PY) iotlab.py discover --sweep

scan:
	$(PY) iotlab.py scan --sweep --hosts $(HOSTS)

agent:
	$(PY) iotlab.py agent "哪些设备能跑 agent"

sim:
	$(PY) iotlab.py sim "打开客厅灯并关掉书房插座"

mcp:
	$(PY) mcp_server.py

test:
	$(PY) -m unittest discover -s tests -v

clean:
	rm -rf __pycache__ tests/__pycache__ iot_agent_lab/__pycache__ iot_agent_lab/access/__pycache__
