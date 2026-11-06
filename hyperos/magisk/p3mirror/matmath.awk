# usage: awk -v op=... -v A="16 floats" -v B="16 floats" -f matmath.awk   (column-major mat4, 3x3 part used)
#  op=div  -> A * inverse(B), printed column-major (16 floats)
#  op=sat  -> saturation factor of A assuming a Rec.709-luma saturation matrix
function ld(s, m,   v, n, r, c) { n = split(s, v); if (n != 16) return 0
  for (c = 0; c < 3; c++) for (r = 0; r < 3; r++) m[r, c] = v[c*4 + r + 1]; return 1 }
BEGIN {
  if (!ld(A, a)) exit 1
  if (op == "sat") { printf "%.4f\n", (a[0,0] - 0.2126) / (1 - 0.2126); exit }
  if (!ld(B, b)) exit 1
  det = b[0,0]*(b[1,1]*b[2,2]-b[1,2]*b[2,1]) - b[0,1]*(b[1,0]*b[2,2]-b[1,2]*b[2,0]) + b[0,2]*(b[1,0]*b[2,1]-b[1,1]*b[2,0])
  if (det == 0) exit 1
  i[0,0] =  (b[1,1]*b[2,2]-b[1,2]*b[2,1])/det; i[0,1] = -(b[0,1]*b[2,2]-b[0,2]*b[2,1])/det; i[0,2] =  (b[0,1]*b[1,2]-b[0,2]*b[1,1])/det
  i[1,0] = -(b[1,0]*b[2,2]-b[1,2]*b[2,0])/det; i[1,1] =  (b[0,0]*b[2,2]-b[0,2]*b[2,0])/det; i[1,2] = -(b[0,0]*b[1,2]-b[0,2]*b[1,0])/det
  i[2,0] =  (b[1,0]*b[2,1]-b[1,1]*b[2,0])/det; i[2,1] = -(b[0,0]*b[2,1]-b[0,1]*b[2,0])/det; i[2,2] =  (b[0,0]*b[1,1]-b[0,1]*b[1,0])/det
  for (r = 0; r < 3; r++) for (c = 0; c < 3; c++) { s = 0; for (k = 0; k < 3; k++) s += a[r,k]*i[k,c]; p[r,c] = s }
  for (c = 0; c < 3; c++) { for (r = 0; r < 3; r++) printf "%.5f ", p[r,c]; printf "0 " }
  print "0 0 0 1"
}
