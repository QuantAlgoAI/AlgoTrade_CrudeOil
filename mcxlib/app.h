#ifndef APP_H
#define APP_H


class App
{
public:
    int x,y;
    App();
    void wait(int _time = 1000);
    void say( char const *s = "");
    void error( char const *s = "");

};

#endif // APP_H
