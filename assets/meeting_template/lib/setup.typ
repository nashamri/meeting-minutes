#import "constants.typ": *

#let index-pages = 2

// Footer definitions
#let footer = [
    #place(dy: -0.6cm)[
        #grid(
            columns: (1fr, 1cm),
            align: (right + horizon, center + horizon),
            [ #meeting-name #sym.space.med #sym.paren.stroked.r الجلسة #meeting-number #sym.paren.stroked.l #sym.space.med #meeting-date],
            [ #box(stroke: 0.05em + rgb(accent-color), inset: 0.3em)[ #context counter(page).display()]],
        )
    ]
    #align(center)[#image("/lib/footer.png")]
]

#let center-footer = [
    #place(dy: -0.6cm)[
        #grid(
            columns: 1fr,
            align: (center + horizon),
            [ #meeting-name #sym.space.med #sym.paren.stroked.r الجلسة #meeting-number #sym.paren.stroked.l #sym.space.med #meeting-date],
        )
    ]
    #align(center)[#image("/lib/footer.png")]
]

// Main setup function that applies all styling
#let setup(doc) = {
    set page(
        "a4",
        numbering: n => {
            if n <= index-pages {
                none
            } else {
                str(n - index-pages)
            }
        },
        margin: (
            top: 4.2cm,
            bottom: 2.4cm,
            left: 1cm,
            right: 1cm,
        ),

        header-ascent: 0.3cm,
        header: [#align(center)[
            #image("/lib/header.png")
            #place(dx: -7.8cm, dy: -0.2cm)[
                #sym.paren.r.stroked سري وغير قابل للتداول #sym.paren.l.stroked]]
            #v(1.2em)
        ],
        footer: context {
            let current-page = counter(page).get().first()
            let final-page = counter(page).final().first()
    
            if (
                current-page <= index-pages
                or current-page == final-page
            ) [#center-footer] else [#footer]
        },

    )
    set heading(numbering: "1")
    set text(lang: "ar", font: "Adobe", size: 14pt)
    set par(justify: true)
    show heading: heading => [
        #heading.body
    ]
    set outline.entry(fill: line(length: 100%, stroke: 0.02em + rgb(accent-color)))
    show outline.entry: it => link(
        it.element.location(),
        it.indented(
            box(width: 10%, height: 5%)[
                *الموضوع #it.prefix():*],
            box(width: 90%, height: 5%)[
                #set par(justify: true)
                #it.inner()
            ]
        ),
    )

    doc
}

// Meeting header section (first page content before members table)
#let meeting-header = [
    #set align(center)
    #text(size: 13pt)[محضر اجتماع]
    #table(
        columns: (1fr, 2fr, 1fr, 1.2fr),
        align: (center, center, center, center),
        stroke: 0.04em,
        fill: (x, y) => if x == 0 or x == 2 { rgb(light-accent-color) },
        inset: 1em,
        [اسم اللجنة / المجلس], [ #meeting-name ], [التاريخ], [ #meeting-date ],
        [الجلسة], [ #meeting-number], [الوقت], [#meeting-time],
    )

    #v(2em)
    #align(right)[ #text(size: 13pt)[أعضاء المجلس حسب قرار تكوينه: ] ]
]

// Agenda section (outline/table of contents)
#let agenda-section = [
    #pagebreak()
    #v(1em)
    #outline(title: align(center)[جدول الأعمال] + v(1em))
    #pagebreak()
]

// Recommendation text before signatures table
#let recommendation(approval-text) = [
    #v(1em)
    #align(right)[
        #text(size: 13pt)[#approval-text]
    ]

    #v(1em)
    #align(right)[ #text(size: 13pt)[رأي الأعضاء في المحضر: ] ]
]

// Closing notes after signatures table
#let closing-notes(invitees: [لا يوجد], notes: []) = [
    #set align(right)

    #v(1em)
    #align(right)[ #text(size: 13pt)[المدعوين في المحضر: #invitees] ]

    #v(1em)
    #align(right)[ #text(size: 13pt)[#notes] ]

    #v(1em)
    #repeat([.])
    #repeat([.])
]
