COMPANY_KNOWLEDGE = {
    "services": (
        "Frostrek LLP provides end-to-end technology solutions including "
        "AI & Machine Learning integration, custom software development, "
        "cloud infrastructure setup, data analytics & business intelligence, "
        "and digital transformation consulting for businesses of all sizes."
    ),
    "about": (
        "Frostrek LLP is a technology company focused on building intelligent "
        "solutions that drive business growth. Founded with the mission of "
        "making advanced technology accessible, Frostrek combines innovation "
        "with practical implementation to deliver measurable results."
    ),
    "benefits": (
        "Why choose Frostrek: rapid delivery timelines, dedicated project managers, "
        "transparent pricing with no hidden costs, 24/7 technical support, "
        "scalable solutions that grow with your business, and proven expertise "
        "across industries including finance, healthcare, and e-commerce."
    ),
    "team_leadership": (
        "Frostrek is led by a team of experienced technologists and business strategists. "
        "The leadership brings expertise from top product companies and consulting firms, "
        "ensuring world-class delivery standards."
    ),
    "academic_collaborations": (
        "Frostrek partners with universities and academic institutions for research "
        "collaborations, internship programs, and talent development initiatives. "
        "These partnerships drive innovation and create pathways for emerging talent."
    ),
    "contact": (
        "Contact Frostrek: Email at info@frostrek.com, visit frostrek.com, "
        "or reach out through our social media channels."
    ),
    "faq": (
        "Q: What industries does Frostrek serve? A: Finance, healthcare, e-commerce, education, and more.\n"
        "Q: Does Frostrek offer support after project delivery? A: Yes, 24/7 technical support is included.\n"
        "Q: Can Frostrek handle small projects? A: Absolutely, we work with businesses of all sizes.\n"
        "Q: What technologies does Frostrek specialize in? A: Python, AI/ML, cloud platforms (AWS, GCP, Azure), and modern web frameworks."
    ),
    "talent_hunt": (
        "Frostrek Talent Hunt is a program designed to identify and nurture top tech talent "
        "through coding challenges, hackathons, and mentorship opportunities."
    ),
}


def get_company_knowledge() -> str:
    sections = [
        ("Services", COMPANY_KNOWLEDGE.get("services", "")),
        ("About", COMPANY_KNOWLEDGE.get("about", "")),
        ("Benefits", COMPANY_KNOWLEDGE.get("benefits", "")),
        ("Team & Leadership", COMPANY_KNOWLEDGE.get("team_leadership", "")),
        ("Academic Collaborations", COMPANY_KNOWLEDGE.get("academic_collaborations", "")),
        ("Contact", COMPANY_KNOWLEDGE.get("contact", "")),
        ("FAQ", COMPANY_KNOWLEDGE.get("faq", "")),
        ("Talent Hunt", COMPANY_KNOWLEDGE.get("talent_hunt", "")),
    ]

    parts: list[str] = []
    for title, content in sections:
        content = (content or "").strip()
        if not content:
            continue
        parts.append(f"## {title}\n{content}")

    return "\n\n".join(parts).strip()


def fetch_knowledge_from_db() -> dict[str, str]:
    """
    Fetch and parse knowledge chunks from Supabase.
    Handles semi-structured blocks with 'Category :-' markers.
    """
    try:
        from db_config import get_connection, release_connection
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT category, chunk_text 
                FROM knowledge_base 
                WHERE chunk_text IS NOT NULL
                  AND chunk_text != ''
                """
            )
            rows = cur.fetchall()
            cur.close()
            
            kb_map = {
                "services": "", "about": "", "benefits": "",
                "team_leadership": "", "academic_collaborations": "", "navigation": "",
                "blogs": "", "industry_partners": "", "client_team_feedback": "",
                "faq": "", "contact": "", "greeting": "", "talent_hunt": "",
                "reference_estimation": ""
            }
            
            import re
            # Pattern to find 'Something :-' or 'Something :' at start of lines or within text
            # We use a broad match and then map to our internal keys
            marker_pattern = re.compile(r"([a-zA-Z &]+)\s?[:-]{1,2}\s?", re.IGNORECASE)

            key_map = {
                "services": "services",
                "about": "about",
                "company": "about",
                "general": "benefits",
                "benefits": "benefits",
                "team & leadership": "team_leadership",
                "team leadership": "team_leadership",
                "campus & academic collaborations": "academic_collaborations",
                "academic collaborations": "academic_collaborations",
                "academic": "academic_collaborations",
                "navigations": "navigation",
                "navigation": "navigation",
                "blog": "blogs",
                "blogs": "blogs",
                "industry partners": "industry_partners",
                "client or team feedback": "client_team_feedback",
                "faqs": "faq",
                "faq": "faq",
                "contact": "contact",
                "greeting": "greeting",
                "talent hunt": "talent_hunt",
                "talenthunt": "talent_hunt",
                "talentpool": "talent_hunt",
                "reference estimation": "reference_estimation"
            }

            for cat_col, text in rows:
                if not text: continue
                
                # Split text by marker pattern while keeping context
                parts = marker_pattern.split(text)
                
                if len(parts) == 1:
                    # No internal markers found (like "benefits :-")
                    # Use the row's category column as the key
                    target_key = key_map.get(cat_col.lower().strip())
                    if target_key:
                        kb_map[target_key] = (kb_map[target_key] + "\n" + text).strip()
                    else:
                        # Fallback: if unknown category, add it to 'about' or keep as is?
                        # For now, let's just append to unclassified or skip
                        pass
                else:
                    # Internal markers found (e.g. "greeting :- ... benefits :- ...")
                    # parts[0] is text before first marker (usually empty)
                    # parts[1] is first marker, parts[2] is first content...
                    
                    for i in range(1, len(parts), 2):
                        marker = parts[i].strip().lower()
                        content = parts[i+1].strip() if (i+1) < len(parts) else ""
                        
                        target_key = key_map.get(marker)
                        if target_key:
                            kb_map[target_key] = (kb_map[target_key] + "\n" + content).strip()
            
            # Final cleanup: if any keys are still empty from parsing, 
            # try to fill from static COMPANY_KNOWLEDGE as safety
            for k, v in kb_map.items():
                if not v:
                    kb_map[k] = COMPANY_KNOWLEDGE.get(k, "")
                # Limit length to avoid blowing up prompt
                if len(kb_map[k]) > 4000:
                    kb_map[k] = kb_map[k][:4000] + "..."
                    
            return kb_map
        finally:
            release_connection(conn)
    except Exception as e:
        from logger_config import logger
        logger.warning("Failed to fetch/parse Supabase KB: %s", e)
        return COMPANY_KNOWLEDGE



