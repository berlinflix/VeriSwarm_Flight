CFG_TEE_TA_LOG_LEVEL ?= 1

# The TA binary is named after its UUID (this becomes <UUID>.ta after signing).
BINARY = 7e9a4c10-3b62-4d8e-a1f5-9c2b6d04e7a3

-include $(TA_DEV_KIT_DIR)/mk/ta_dev_kit.mk

ifeq ($(wildcard $(TA_DEV_KIT_DIR)/mk/ta_dev_kit.mk), )
clean:
	@echo 'Error: TA_DEV_KIT_DIR is not set to a valid OP-TEE TA dev kit.'
	@echo 'Point it at .../export-ta_arm64 from the matching OP-TEE build.'
	@echo 'See optee/README.md.'
endif
