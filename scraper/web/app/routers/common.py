from fastapi import Request


def render(request: Request, template: str, **context):
    context.update(
        request=request,
        active_path=request.url.path,
        settings=request.app.state.settings,
    )
    return request.app.state.templates.TemplateResponse(request=request, name=template, context=context)


def bridge(request: Request):
    return request.app.state.control
