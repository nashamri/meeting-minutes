#import "constants.typ": light-accent-color, body-font

#let topic(title, body, decision, articles, target) = {

set text(lang: "ar", font: body-font)
set par(justify: true)

table(columns:(0.5cm, 2.2cm, 1fr),
align:horizon+center,
stroke:0.3pt+ {rgb("#000000")},
inset: 1em,
fill: (x, y) => if x == 1 { rgb( light-accent-color )},
[1],
[الموضوع  #context counter(heading).display(n => str(n + 1)) ],
[
  #align(center)[= #title]
],

[2],
[ملخص وصف الموضوع ومناقشته],
[#align(right)[
  #body
]],

[3],
[القرار/التوصية],
[#align(right)[*#decision*]],

[4],
[مستند القرار/التوصية],
[#align(right)[#text(size: 11pt)[#articles]]],


[5],
[الجهة ذات العلاقة],
[#align(right)[#target]],
)

pagebreak(weak: true)

}
