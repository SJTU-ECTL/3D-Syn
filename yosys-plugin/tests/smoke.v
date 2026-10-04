module child(input [3:0] a, output [3:0] y);
  assign y = a;
endmodule

module top(
  input clk,
  input [3:0] a,
  input [3:0] b,
  output [4:0] sum,
  output [3:0] passthrough
);
  child \\odd$name (.a(a), .y(passthrough));
  assign sum = a + b;
endmodule
