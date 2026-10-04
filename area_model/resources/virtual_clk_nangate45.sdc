# Virtual clock used when generating Nangate45 area-model training samples.
create_clock -name virtual_clk -period 1.0
set_input_delay  0.0 -clock virtual_clk [all_inputs]
set_output_delay 0.0 -clock virtual_clk [all_outputs]
