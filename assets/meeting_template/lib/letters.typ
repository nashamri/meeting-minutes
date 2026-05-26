#import "constants.typ": light-accent-color

#let hr-letter(meeting-number, meeting-date, decision, decision-number, co-author: "") = {
  set page(
    "a4",
    numbering: n => {
      if n <= 2 {
        none
      } else {
        str(n - 2)
      }
    },
    margin: (
      top: 4.2cm,
      bottom: 2.4cm,
      left: 2cm,
      right: 2cm,
    ),

    header-ascent: 0.3cm,
    header: [#align(center)[
        #image("/lib/header.png")]
    ],
    footer: [
      #align(center)[#image("/lib/footer.png")]
    ],
  )

  v(2em)
  set text(lang: "ar", font: "Almudid", size: 13pt, weight: "bold")

  [
    سعادة المشرف على الإدارة العامة للموارد البشرية #h(1fr) سلّمه الله

    السلام عليكم ورحمة الله وبركاته، وبعد:
  ]
  v(1em)
  set text(size: 14pt, font: "Sakkal Majalla", weight: "regular")
  par(justify: true, first-line-indent: 1cm)[
    نهديكم أطيب تحية وتقدير، إشارة إلى موافقة سعادة رئيس الجامعة على محضر الجلسة #meeting-number للمجلس العلمي والمنعقدة بتاريخ #meeting-date، والمتضمن القرار الآتي:
  ]
  v(1em)
  box(stroke: 0.1em, inset: (top: 0.4em, bottom: 1.5em, left: 1em, right: 1em), width: 100%)[
    #align(center)[#text(size: 11pt, weight: "bold")[#underline[قرار المجلس العلمي رقم #decision-number]]]
    #v(0.5em)
    #set par(justify: true)
    #set text(size: 14pt)
    #decision
    ]
  
  v(1em)
  align(center)[
    #par[
      نأمل الاطلاع وإكمال الإجراءات اللازمة حيال ذلك في ضوء الأنظمة واللوائح. \ \
      وتقبلوا تحياتي وتقديري
    ]]

  v(4em)

  set text(size: 13pt, font:"Almudid", weight: "bold")
  grid(
    columns: (3fr, 2fr),
    align: (right+bottom, center),
      [
          #text(size: 7pt)[#co-author]
      ],
    [
      #place(dy:0.8em, dx:-5em)[أمين المجلس العلمي]\
      #image("my-signature.png")
      #place(dy:-1.5em, dx:-4em)[د. ناصر بن عويد الشمري]
    ],
  )
}

#let college-letter(meeting-number, meeting-date, decision, decision-number, college) = {
  set page(
    "a4",
    numbering: n => {
      if n <= 2 {
        none
      } else {
        str(n - 2)
      }
    },
    margin: (
      top: 4.2cm,
      bottom: 2.4cm,
      left: 2cm,
      right: 2cm,
    ),

    header-ascent: 0.3cm,
    header: [#align(center)[
        #image("/lib/header.png")]
    ],
    footer: [
      #align(center)[#image("/lib/footer.png")]
    ],
  )

  set text(lang: "ar", font: "Almudid", size: 13pt, weight: "bold")
v(1em)
  [
    سعادة عميد كلية #college #h(1fr) سلّمه الله

    السلام عليكم ورحمة الله وبركاته، وبعد:
  ]
  v(1em)
  set text(size: 14pt, font: "Sakkal Majalla", weight: "regular")
  par(justify: true, first-line-indent: 1cm)[
    نهديكم أطيب تحية وتقدير، إشارة إلى موافقة سعادة رئيس الجامعة على محضر الجلسة #meeting-number للمجلس العلمي والمنعقدة بتاريخ #meeting-date، والمتضمن القرار الآتي:
  ]
  v(1em)
  box(stroke: 0.1em, inset: (top: 0.4em, bottom: 1.5em, left: 1em, right: 1em), width: 100%)[
    #align(center)[#text(size: 11pt, weight: "bold")[#underline[قرار المجلس العلمي رقم #decision-number]]]
    #v(0.5em)
    #set par(justify: true)
    #set text(size: 14pt)
    #decision
  ]
  v(1em)
  align(center)[
    #par[
      نأمل الاطلاع وإكمال الإجراءات اللازمة حيال ذلك في ضوء الأنظمة واللوائح. \ \
      وتقبلوا تحياتي وتقديري
    ]]

  v(4em)

  set text(font: "Almudid", size: 13pt)
  grid(
    columns: (3fr, 2fr),
    align: (right, center),
    [],
    // text(size: 10pt)[خالد الشمري],
    [
      #place(dy:0.8em, dx:-5em)[أمين المجلس العلمي]\
      #image("my-signature.png")
      #place(dy:-1.5em, dx:-4em)[د. ناصر بن عويد الشمري]
    ],
  )
}

#let uni-letter(title, body, decision, meeting-number, meeting-date, articles, override-decision: "") = {
  set page(
    "a4",
    numbering: n => {
      if n <= 2 {
        none
      } else {
        str(n - 2)
      }
    },
    margin: (
      top: 4.2cm,
      bottom: 2.4cm,
      left: 2cm,
      right: 2cm,
    ),

    header-ascent: 0.3cm,
    header: [#align(center)[
        #image("/lib/header.png")]
    ],
    footer: [
      #align(center)[#image("/lib/footer.png")]
    ],
  )
  set text(lang: "ar", font: "Calibri", size: 16pt, weight: "bold")

  align(center)[مذكرة للعرض على مجلس الجامعة]

  set text(weight: "regular", size: 14pt)
  set par(justify: true)
  table(
    columns: (3cm, 1fr),
    align: (center+horizon, center+horizon),
    stroke: 0.04em,
    inset: 0.5em,
    fill: (x, y) => if x == 0 { rgb(light-accent-color)},
    [من], [*وكيل الجامعة للدراسات العليا والبحث العلمي*],
    [إلى], [*مجلس الجامعة الموقر*],
    [الموضوع], [*#title*],
    par(justify: false)[وصف مفصل للموضوع يتضمن قرارات المجالس المختصة التي تناولت الموضوع],
    [
      #align(right)[#text(size: 11pt)[
          #body
          - توصية المجلس العلمي بجلسته #meeting-number بتاريخ #meeting-date بـ #decision
          *عليه، نقترح على مجلسكم الموقر مايلي:*
          #if override-decision == "" {
              [- #decision]
          } else {
              [- #override-decision]
          }
        ]]],

    [المستند النظامي],
    [
      #align(right)[#text(size: 9pt)[#articles]]
    ],
  )
}
