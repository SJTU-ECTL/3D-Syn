module coarse_liberty_smoke(
  input [3:0] a,
  input [3:0] b,
  output [4:0] sum
);
  assign sum = {1'b0, a} + {1'b0, b};
endmodule

module coarse_dff_smoke(
  input clk,
  input d,
  output reg q
);
  always @(posedge clk) begin
    q <= d;
  end
endmodule
