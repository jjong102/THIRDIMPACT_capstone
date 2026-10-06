# MD200T 기준값과 Jetson Orin Nano 적용 순서

## 1. 기존 MD200T에서 읽은 기준값

**2026-10-06 16:53~16:54 KST에 기존 MD200T에서 직접 읽은 값이다. 이 값을 적용 기준으로 사용한다.** 인휠 모터는 연결하지 않은 상태였다. 아래 표는 두 번째 조회 결과다.

| 통신 항목 | 확인한 값 |
| --- | --- |
| 통신 방식 | RS485 |
| 통신속도 | 115200bps |
| 직렬 형식 | 8N1 |
| 장치 ID | 1 |
| RMID / TMID | 183 / 184 |

숫자는 조회 원시값이며, DATA HEX는 장치에서 받은 바이트 순서 그대로다. `PID[번호]`는 스텝·커브·프리셋의 조회 번호를 나타낸다. 응답한 **188개 항목**을 모두 기록했다.

| PID[번호] | 항목 | 읽은 값 | DATA HEX |
| --- | --- | --- | --- |
| 1 | PID_VER | 72 | `48` |
| 8 | PID_BACKWARD_RATIO | 40 | `28` |
| 9 | PID_PULSE_IN_TYPE | 0 | `00` |
| 16 | PID_INV_SIGN_CMD | 0 | `00` |
| 17 | PID_USE_LIMIT_SW | 0 | `00` |
| 18 | PID_18 | 0 | `00` |
| 19 | PID_INV_ALARM | 0 | `00` |
| 21 | PID_HALL_TYPE | 10 | `0a` |
| 22 | PID_INV_SIGN_OUT | 0 | `00` |
| 23 | PID_23 | 0 | `00` |
| 24 | PID_STOP_STATUS | 1 | `01` |
| 25 | PID_INPUT_TYPE | 0 | `00` |
| 26 | PID_POS_SEN_TYPE | 0 | `00` |
| 27 | PID_27 | 0 | `00` |
| 28 | PID_STOP_STATUS2 | 1 | `01` |
| 29 | PID_29 | 0 | `00` |
| 32 | PID_SAVE_POSI | 0 | `00` |
| 34 | PID_CTRL_STATUS | 0 | `00` |
| 36 | PID_START_INV_SIGN | 0 | `00` |
| 37 | PID_RUN_INV_SIGN | 0 | `00` |
| 38 | PID_REGENERATION | 1 | `01` |
| 39 | PID_CTRL_STATUS2 | 33 | `21` |
| 40 | PID_LIMIT_STOP_COND | 0 | `00` |
| 41 | PID_TQ_LIMIT_SW | 0 | `00` |
| 42 | PID_POSI_INPUT_MODE | 0 | `00` |
| 43 | PID_SINE_CTRL | 1 | `01` |
| 44 | PID_TQ_CTRL | 0 | `00` |
| 45 | PID_BLUETOOTH | 0 | `00` |
| 46 | PID_USE_EPOSI | 0 | `00` |
| 48 | PID_DI | 104 | `68` |
| 49 | PID_IN_POSITION_OK | 0 | `00` |
| 50 | PID_USE_SIGNED_POS | 0 | `00` |
| 51 | PID_OVERLOAD_ALARM_ON | 1 | `01` |
| 52 | PID_DIP_INV | 1 | `01` |
| 53 | PID_DIP_OPEN | 0 | `00` |
| 54 | PID_DIP_CHG | 0 | `00` |
| 55 | PID_RECT_SINE | 0 | `00` |
| 56 | PID_TURN_RATIO | 30 | `1e` |
| 57 | PID_MAX_SS_TIME | 15 | `0f` |
| 58 | PID_DIR_INV_SIGN | 0 | `00` |
| 60 | PID_LATE_ALARM | 33 | `21` |
| 61 | PID_SPEED_OUT_TYPE | 0 | `00` |
| 62 | PID_USE_MAX_TQ | 0 | `00` |
| 63 | PID_USE_CLUTCH | 1 | `01` |
| 64 | PID_INV_POS_SEN | 1 | `01` |
| 65 | PID_HALL2_TYPE | 10 | `0a` |
| 66 | PID_TQ_RATIO | 0 | `00` |
| 68 | PID_68 | 10 | `0a` |
| 71 | PID_USE_ENC_PHASE | 1 | `01` |
| 72 | PID_72 | 0 | `00` |
| 73 | PID_73 | 1 | `01` |
| 74 | PID_LIMIT_STOP_COND | 0 | `00` |
| 75 | PID_COMPLEX_DIR | 0 | `00` |
| 76 | PID_USE_EMER_SW | 0 | `00` |
| 78 | PID_UI_COM | 0 | `00` |
| 79 | PID_MOT_TYPE | 3 | `03` |
| 80 | PID_ENC_INV_DIR | 0 | `00` |
| 81 | PID_FAULT_TYPE | 0 | `00` |
| 82 | PID_SYNC_TYPE | 0 | `00` |
| 83 | PID_TWIN_ALARM_ON | 0 | `00` |
| 84 | PID_NO_MODBUS | 0 | `00` |
| 86 | PID_USE_RC_LIMIT_SW | 0 | `00` |
| 87 | PID_INIT_SET_OK | 0 | `00` |
| 89 | PID_STOP_ALL_BY_LSW | 0 | `00` |
| 90 | PID_INT_SPEED_INV_SIGN | 0 | `00` |
| 91 | PID_91 | 10 | `0a` |
| 92 | PID_STALL_ALARM_ON | 1 | `01` |
| 93 | PID_93 | 0 | `00` |
| 101 | PID_ALARM_TQ | 112 | `70 00` |
| 102 | PID_ALARM_TQ_DELAY | 400 | `90 01` |
| 103 | PID_CLUTCH_STOP_DELAY | 500 | `f4 01` |
| 104 | PID_MAX_OUT | 1023 | `ff 03` |
| 105 | PID_105 | 50 | `32 00` |
| 108 | PID_108 | 100 | `64 00` |
| 109 | PID_SLOW_START2 | 100 | `64 00` |
| 111 | PID_111 | 100 | `64 00` |
| 112 | PID_SLOW_DOWN2 | 100 | `64 00` |
| 113 | PID_113 | 100 | `64 00` |
| 114 | PID_POSI_SS2 | 100 | `64 00` |
| 115 | PID_115 | 100 | `64 00` |
| 116 | PID_116 | 100 | `64 00` |
| 117 | PID_117 | 0 | `00 00` |
| 118 | PID_TAR_VEL2 | 0 | `00 00` |
| 119 | PID_119 | 0 | `00 00` |
| 120 | PID_IN_POSITION2 | 0 | `00 00` |
| 121 | PID_121 | 400 | `90 01` |
| 122 | PID_MAX_RPM2 | 400 | `90 01` |
| 123 | PID_123 | 4096 | `00 10` |
| 124 | PID_MIN_SSSD | 0 | `00 00` |
| 133 | PID_ID | 1 | `01` |
| 136 | PID_136 | 0 | `00 00` |
| 137 | PID_ECAN_BITRATE | 1 | `01` |
| 138 | PID_INT_RPM_DATA | 0 | `00 00` |
| 139 | PID_TQ_DATA | 0 | `00 00` |
| 143 | PID_VOLT_IN | 281 | `19 01` |
| 145 | PID_145 | 0 | `00 00` |
| 149 | PID_RETURN_TYPE | 0 | `00` |
| 153 | PID_SLOW_START | 100 | `64 00` |
| 154 | PID_SLOW_DOWN | 100 | `64 00` |
| 155 | PID_TAR_VEL | 0 | `00 00` |
| 156 | PID_ENC_PPR | 4096 | `00 10` |
| 157 | PID_LOW_SPEED_LIMIT | 5 | `05 00` |
| 158 | PID_HIGH_SPEED_LIMIT | 1000 | `e8 03` |
| 159 | PID_QUICK_SLOW_DOWN | 100 | `64 00` |
| 161 | PID_161 | 1 | `01 00` |
| 162 | PID_DEAD_ZONE | 100 | `64 00` |
| 166 | PID_REF_RPM | 0 | `00 00` |
| 167 | PID_PV_GAIN | 20 | `14 00` |
| 168 | PID_P_GAIN | 6000 | `70 17` |
| 169 | PID_I_GAIN | 6000 | `70 17` |
| 170 | PID_ENC_PPR2 | 4096 | `00 10` |
| 171 | PID_IN_POSITION | 0 | `00 00` |
| 172 | PID_LOW_POT_LIMIT | 0 | `00 00` |
| 173 | PID_HIGH_POT_LIMIT | 0 | `00 00` |
| 176 | PID_TAR_POSI_VEL | 1110 | `56 04` |
| 177 | PID_PNT_VEL_DATA | HEX 원본 참조 | `00 00 00 00 07 00 21` |
| 178 | PID_POSI_SS | 100 | `64 00` |
| 179 | PID_179 | 100 | `64 00` |
| 180 | PID_COM_TAR_SPEED | 1000 | `e8 03` |
| 181 | PID_CW_MAX_RPM | 0 | `00 00` |
| 182 | PID_CCW_MAX_RPM | 0 | `00 00` |
| 183 | PID_FUNC_CMD_TYPE | 0 | `00 00` |
| 185 | PID_COM_WATCH_DELAY | 0 | `00 00` |
| 187 | PID_TQ_LIMIT_SW_VAL | 58 | `3a 00` |
| 190 | PID_MAX_OPEN_OUT | 100 | `64 00` |
| 191 | PID_TOUR_DATA | 1000, 10 | `e8 03 0a 00` |
| 193 | PID_MAIN_DATA | HEX 원본 참조 | `00 00 00 00 07 00 00 00 00 00 00 00 00 00 00 1f 00` |
| 194 | PID_IO_MONITOR | HEX 원본 참조 | `00 00 00 00 00 68 00 00 00 04 00 00 19 01 0a 0a 66` |
| 195 | PID_TAR_POSI | 0 | `00 00 00 00` |
| 196 | PID_MONITOR | HEX 원본 참조 | `00 00 00 00 00 00 00 00 00 00 00 68` |
| 197 | PID_POSI_DATA | HEX 원본 참조 | `00 00 00 00 00 00 00 00` |
| 199 | PID_INC_TAR_POSI | 0 | `00 00 00 00` |
| 200 | PID_MAIN_DATA2 | HEX 원본 참조 | `00 00 00 00 00 00 00 00 00 21 00 00 00 00 00 1f 00` |
| 201 | PID_MONITOR2 | HEX 원본 참조 | `00 00 00 00 00 00 21 00 00 00 00 68` |
| 202 | PID_IO_MONITOR2 | HEX 원본 참조 | `00 00 00 00 21 68 00 00 00 07 00 00 19 01 0a 0a 66` |
| 203 | PID_GAIN | 위치P=20, 속도P=6000, 속도I=6000 | `14 00 70 17 70 17` |
| 204 | PID_POSI_VEL_DATA | HEX 원본 참조 | `00 00 00 00 00 00 00` |
| 205 | PID_TYPE | MD200T-V7.2e-H100-E | `4d 44 32 30 30 54 2d 56 37 2e 32 65 2d 48 31 30 30 2d 45` |
| 210 | PID_PNT_MAIN_DATA | HEX 원본 참조 | `00 00 00 00 00 00 00 00 00 00 00 00 00 21 00 00 00 00` |
| 211 | PID_MAX_LOAD | 118 | `76 00` |
| 216 | PID_PNT_MONITOR | HEX 원본 참조 | `00 00 00 00 00 00 00 00 00 21 00 00 00 00` |
| 221 | PID_MAX_RPM | 400 | `90 01` |
| 222 | PID_SPEED_LIMIT | 최저=5, 최고=1000 | `05 00 e8 03` |
| 223 | PID_MIN_RPM | 1 | `01 00` |
| 228 | PID_MIN_LIMIT_POS | 0 | `00 00 00 00` |
| 229 | PID_ALARM_LOG | HEX 원본 참조 | `21 00 00 00 01 00 14 00 05 09 00 00` |
| 230 | PID_REF_POSI | 0 | `00 00 00 00` |
| 231 | PID_POSI_MIN_LIMIT | -1023 | `01 fc ff ff` |
| 232 | PID_POSI_CEN | 0 | `00 00 00 00` |
| 233 | PID_POSI_MAX_LIMIT | 1023 | `ff 03 00 00` |
| 234 | PID_TIME | 0 | `00 00 00 00` |
| 235 | PID_TQ_GAIN | P=10, I=20 | `0a 00 14 00` |
| 239 | PID_FUNC_SPEED | 1000, 10 | `e8 03 0a 00` |
| 240 | PID_MAX_LIMIT_POS | 0 | `00 00 00 00` |
| 241 | PID_PNT_IO_MONITOR | HEX 원본 참조 | `68 68 04 07 00 00 00 00 19 01 0a 66 00` |
| 249 | PID_INIT_SET_RPM | 300 | `2c 01` |
| 250 | PID_FUNC_POSI | 1500, 240 | `dc 05 f0 00` |
| 225[1] | PID_STEP_INPUT | 57 rpm | `01 39 00` |
| 225[2] | PID_STEP_INPUT | 114 rpm | `02 72 00` |
| 225[3] | PID_STEP_INPUT | 171 rpm | `03 ab 00` |
| 225[4] | PID_STEP_INPUT | 228 rpm | `04 e4 00` |
| 225[5] | PID_STEP_INPUT | 285 rpm | `05 1d 01` |
| 225[6] | PID_STEP_INPUT | 342 rpm | `06 56 01` |
| 225[7] | PID_STEP_INPUT | 400 rpm | `07 90 01` |
| 226[1] | PID_CURVE_PT | x=0, y=0 | `01 00 00 00 00` |
| 226[2] | PID_CURVE_PT | x=0, y=0 | `02 00 00 00 00` |
| 226[3] | PID_CURVE_PT | x=0, y=0 | `03 00 00 00 00` |
| 226[4] | PID_CURVE_PT | x=0, y=0 | `04 00 00 00 00` |
| 227[1] | PID_PRESET_DATA | M1=0, M2=0 | `01 00 00 00 00 00 00 00 00` |
| 227[2] | PID_PRESET_DATA | M1=0, M2=0 | `02 00 00 00 00 00 00 00 00` |
| 227[3] | PID_PRESET_DATA | M1=0, M2=0 | `03 00 00 00 00 00 00 00 00` |
| 227[4] | PID_PRESET_DATA | M1=0, M2=0 | `04 00 00 00 00 00 00 00 00` |
| 227[5] | PID_PRESET_DATA | M1=0, M2=0 | `05 00 00 00 00 00 00 00 00` |
| 227[6] | PID_PRESET_DATA | M1=0, M2=0 | `06 00 00 00 00 00 00 00 00` |
| 227[7] | PID_PRESET_DATA | M1=0, M2=0 | `07 00 00 00 00 00 00 00 00` |
| 227[8] | PID_PRESET_DATA | M1=0, M2=0 | `08 00 00 00 00 00 00 00 00` |
| 227[9] | PID_PRESET_DATA | M1=0, M2=0 | `09 00 00 00 00 00 00 00 00` |
| 227[10] | PID_PRESET_DATA | M1=0, M2=0 | `0a 00 00 00 00 00 00 00 00` |
| 227[11] | PID_PRESET_DATA | M1=0, M2=0 | `0b 00 00 00 00 00 00 00 00` |
| 227[12] | PID_PRESET_DATA | M1=0, M2=0 | `0c 00 00 00 00 00 00 00 00` |
| 227[13] | PID_PRESET_DATA | M1=0, M2=0 | `0d 00 00 00 00 00 00 00 00` |
| 227[14] | PID_PRESET_DATA | M1=0, M2=0 | `0e 00 00 00 00 00 00 00 00` |
| 227[15] | PID_PRESET_DATA | M1=0, M2=0 | `0f 00 00 00 00 00 00 00 00` |
| 227[16] | PID_PRESET_DATA | M1=0, M2=0 | `10 00 00 00 00 00 00 00 00` |
| 227[17] | PID_PRESET_DATA | M1=0, M2=0 | `11 00 00 00 00 00 00 00 00` |
| 227[18] | PID_PRESET_DATA | M1=0, M2=0 | `12 00 00 00 00 00 00 00 00` |
| 227[19] | PID_PRESET_DATA | M1=0, M2=0 | `13 00 00 00 00 00 00 00 00` |
| 227[20] | PID_PRESET_DATA | M1=0, M2=0 | `14 00 00 00 00 00 00 00 00` |

## 2. Jetson Orin Nano에 적용하는 순서

1. 이 `serial_test` 패키지와 README를 Jetson으로 복사하고, 사용하는 ROS 2 환경과 `pyserial`을 준비한다.
2. MD200T에 전원을 공급하고 USB-RS485 변환기를 Jetson에 연결한다. 실제 시리얼 포트 경로와 접근 권한을 확인한다.
3. `serial_test/motor_driver.py`의 포트를 실제 연결 경로로 맞추고 **115200bps / 8N1 / ID 1 / RMID 183 / TMID 184**를 사용한다. 기존 `write_BAUD()`는 호출하지 않는다.
4. **같은 물리 드라이버를 옮겼다면 기존 설정을 그대로 사용한다.** 교체 MD200T에 이식한다면 위 표의 설정 항목을 기준값 그대로 적용한다. 모터 타입 **PID 79 = 3**을 먼저 적용하고, 세부 설정과 게인을 뒤에 적용한다. ID와 통신속도 변경은 마지막에 한다. 펌웨어 식별·현재 상태·알람·피드백 값은 조회 기록이므로 쓰기 대상에 포함하지 않는다. 항목명이 PID 번호만으로 표시된 값은 쓰기 의미가 확인되지 않은 항목이다.
5. 설정을 적용한 장치는 전원을 다시 넣고 기준 설정이 유지되는지 조회한다. 비교 기준은 이 README의 값이다. 다른 값을 새 기준으로 저장하지 않는다.
6. 인휠 모터를 연결하고 바퀴를 띄운 상태에서 좌우 채널·방향·저속 구동·정지를 확인한다. `/cmd_vel` 중단과 RS485 단절 시 정지도 각각 확인한다.
7. Jetson에서 패키지를 빌드하고 ROS 노드로 저속 시험한 뒤, 바닥에서 직진·회전·오도메트리를 확인한다.
